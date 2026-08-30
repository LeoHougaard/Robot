package com.leo.pixelrobot.policy

import java.io.File
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.json.JSONArray
import org.json.JSONObject

class PolicyMathTest {
    private val calibration by lazy {
        RobotCalibration.parse(
            File("src/main/assets/assembly-four-leg-linkage-12dof.calibration.json").readText(),
        )
    }

    @Test
    fun zeroPolicyPoseMapsToMeasuredCenters() {
        val targets = calibration.servoTargets(FloatArray(12))
        val reconstructed = calibration.policyPositions(targets)
        assertArrayEquals(FloatArray(12), reconstructed, 1.0e-6f)
    }

    @Test
    fun fourBarKneeRoundTripsWithFemurMotion() {
        val pose = floatArrayOf(0f, 0.1f, -0.2f, 0f, -0.1f, 0.2f, 0f, 0.1f, -0.2f, 0f, -0.1f, 0.2f)
        val reconstructed = calibration.policyPositions(calibration.servoTargets(pose))
        assertArrayEquals(pose, reconstructed, 1.0e-5f)
    }

    @Test
    fun standCaptureUpdatesZerosAndPreservesRelativeSafeTravel() {
        val shifts = FloatArray(12) { index -> (index % 3 - 1) * 2.5f }
        val measured = calibration.servoIds.indices.associate { index ->
            calibration.servoIds[index] to calibration.zeros[index] + shifts[index]
        }
        val captured = calibration.captureReference(measured)
        val updated = captured.calibration
        assertTrue(JSONObject(captured.json).getBoolean("calibrated"))
        assertEquals(2.5f, captured.largestShiftDegrees, 1.0e-6f)
        calibration.servoIds.indices.forEach { index ->
            assertEquals(measured[calibration.servoIds[index]], updated.zeros[index])
            assertEquals(calibration.minimums[index] + shifts[index], updated.minimums[index], 1.0e-5f)
            assertEquals(calibration.maximums[index] + shifts[index], updated.maximums[index], 1.0e-5f)
            assertEquals(calibration.parentIndex[index], updated.parentIndex[index])
        }
        assertEquals(0f, updated.maximumReferenceErrorDegrees(measured), 1.0e-6f)
    }

    @Test
    fun standCaptureRejectsAReferenceFarFromTheKnownAssembly() {
        val measured = calibration.servoIds.indices.associate { index ->
            calibration.servoIds[index] to calibration.zeros[index] + if (index == 0) 46f else 0f
        }
        val error = runCatching { calibration.captureReference(measured) }.exceptionOrNull()
        assertTrue(error is IllegalArgumentException)
        assertTrue(error?.message.orEmpty().contains("previous reference"))
    }

    @Test
    fun capturedCalibrationReplacesEsp32LimitsAndVerifiesReadback() {
        val firmwareConfig = JSONObject().put(
            "servos",
            JSONArray((1..12).map { servoId ->
                JSONObject()
                    .put("id", servoId)
                    .put("name", "servo $servoId")
                    .put("min", 100)
                    .put("home", 180)
                    .put("max", 260)
                    .put("invert", false)
                    .put("enabled", true)
                    .put("monitor", true)
                    .put("monitorInterval", 250)
            }),
        )
        val updatedServos = calibration.firmwareServoConfiguration(firmwareConfig)
        val readback = JSONObject().put("servos", updatedServos)

        calibration.requireFirmwareConfigurationMatches(readback)
        calibration.servoIds.indices.forEach { policyIndex ->
            val servoId = calibration.servoIds[policyIndex]
            val servo = (0 until updatedServos.length())
                .map(updatedServos::getJSONObject)
                .single { it.getInt("id") == servoId }
            assertEquals(calibration.minimums[policyIndex].toDouble(), servo.getDouble("min"), 1.0e-6)
            assertEquals(calibration.zeros[policyIndex].toDouble(), servo.getDouble("home"), 1.0e-6)
            assertEquals(calibration.maximums[policyIndex].toDouble(), servo.getDouble("max"), 1.0e-6)
        }

        readback.getJSONArray("servos").getJSONObject(0).put("home", 180)
        val error = runCatching { calibration.requireFirmwareConfigurationMatches(readback) }.exceptionOrNull()
        assertTrue(error is IllegalArgumentException)
        assertTrue(error?.message.orEmpty().contains("captured zero"))
    }

    @Test
    fun stationaryAccelerationInitializesProjectedGravity() {
        val estimator = GravityEstimator()
        val value = estimator.update(floatArrayOf(0f, 0f, 1000f), FloatArray(3), -1f, 0.02f)
        assertArrayEquals(floatArrayOf(0f, 0f, -1f), value, 1.0e-6f)
        assertEquals(1f, norm(value), 1.0e-6f)
    }

    @Test
    fun usbCPortsRearImuMappingIsLocked() {
        assertArrayEquals(
            floatArrayOf(0f, -1f, 0f),
            calibration.bodyAccelerationMg(floatArrayOf(1f, 0f, 0f)),
            1.0e-6f,
        )
        assertArrayEquals(
            floatArrayOf(1f, 0f, 0f),
            calibration.bodyAccelerationMg(floatArrayOf(0f, 1f, 0f)),
            1.0e-6f,
        )

        val value = JSONObject(File("src/main/assets/assembly-four-leg-linkage-12dof.calibration.json").readText())
        value.getJSONObject("imu").put(
            "body_axis_from_sensor_axis",
            JSONArray(List(3) { row -> List(3) { column -> if (row == column) 1 else 0 } }),
        )
        val error = runCatching { RobotCalibration.parse(value.toString()) }.exceptionOrNull()
        assertTrue(error is IllegalArgumentException)
        assertTrue(error?.message.orEmpty().contains("USB-C ports facing rear"))
    }

    @Test
    fun sessionGyroZeroOverridesStoredBiasWithoutChangingAxisMapping() {
        val sensorBias = floatArrayOf(2.5f, -3f, -19.5f)
        assertArrayEquals(
            FloatArray(3),
            calibration.bodyGyroRadPerSecond(sensorBias, sensorBias),
            1.0e-6f,
        )
    }

    @Test
    fun legacyPolicyContractPreservesDeployedActionMath() {
        val metadata = JSONObject(File("src/main/assets/policy_metadata.json").readText())
        metadata.remove("action_contract")
        metadata.remove("stationary_action_contract")
        metadata.put("action_clip", JSONArray(listOf(-1.0, 1.0)))
        metadata.put("action_delta_limit", 0.3)
        metadata.put("action_scale_rad", 0.25)
        val contract = PolicyContract.parse(metadata.toString())
        val step = contract.applyAction(
            FloatArray(12) { 1f },
            FloatArray(12),
            FloatArray(12),
            floatArrayOf(0.1f, 0f, 0f),
        )
        assertArrayEquals(FloatArray(12) { 0.3f }, step.applied, 1.0e-6f)
        assertEquals(0.25f, contract.positionTargetScaleRadians, 1.0e-6f)
    }

    @Test
    fun v2PolicyContractMatchesIsaacActionOrder() {
        val metadata = JSONObject(File("src/main/assets/policy_metadata.json").readText())
        metadata.put("schema_version", 2)
        metadata.put("command_smoothing_time_s", 0.4)
        metadata.put(
            "action_contract",
            JSONObject()
                .put("actor_output_clip", JSONArray(listOf(-1.0, 1.0)))
                .put(
                    "applied_normalized_clip_by_joint",
                    JSONArray(List(4) { listOf(0.4, 1.0, 1.0) }.flatten()),
                )
                .put("low_pass_alpha", 0.2)
                .put("applied_normalized_slew_limit", 0.2)
                .put("position_target_scale_rad", 0.3),
        )
        metadata.put(
            "stationary_action_contract",
            JSONObject()
                .put("behavior", "slew_to_validated_four_foot_stance_action")
                .put("normalized_stance_action", JSONArray(List(12) { 0.0 }))
                .put("planar_command_deadband_m_s", 0.02)
                .put("yaw_command_deadband_rad_s", 0.03),
        )
        val contract = PolicyContract.parse(metadata.toString())
        val moving = contract.applyAction(
            FloatArray(12) { 1f },
            FloatArray(12),
            FloatArray(12),
            floatArrayOf(0.1f, 0f, 0f),
        )
        assertArrayEquals(
            floatArrayOf(0.08f, 0.2f, 0.2f, 0.08f, 0.2f, 0.2f, 0.08f, 0.2f, 0.2f, 0.08f, 0.2f, 0.2f),
            moving.applied,
            1.0e-6f,
        )
        val stationary = contract.applyAction(
            FloatArray(12) { 1f },
            moving.filtered,
            moving.applied,
            FloatArray(3),
        )
        assertArrayEquals(
            floatArrayOf(0.064f, 0.16f, 0.16f, 0.064f, 0.16f, 0.16f, 0.064f, 0.16f, 0.16f, 0.064f, 0.16f, 0.16f),
            stationary.applied,
            1.0e-6f,
        )
    }

    @Test
    fun calibrated25HzPolicyControlsRuntimeTiming() {
        val metadata = JSONObject(File("src/main/assets/policy_metadata.json").readText())
            .put("control_hz", 25)
        val contract = PolicyContract.parse(metadata.toString())
        assertEquals(25, contract.controlHz)
        assertEquals(0.04f, contract.controlFrameSeconds, 1.0e-6f)
        assertEquals(40_000_000L, contract.controlFramePeriodNanoseconds)
    }

    @Test
    fun currentV3ObservationHoldsMissingCurrentAndClearsValidity() {
        val metadata = JSONObject(File("src/main/assets/policy_metadata.json").readText())
            .put("schema_version", 3)
            .put("observation_size", 279)
            .put("observation_builder", "current_v3_279")
            .put(
                "current_observation_contract",
                JSONObject()
                    .put("units", "mA")
                    .put("absolute", true)
                    .put("normalization_bias_ma", JSONArray(List(12) { 10.0 }))
                    .put("normalization_scale_ma", JSONArray(List(12) { 20.0 }))
                    .put("clip_normalized", JSONArray(List(12) { 10.0 }))
                    .put("current_step_ma", 6.5)
                    .put("missing_behavior", "hold_last_finite_and_validity_zero"),
            )
            .put(
                "posture_command_contract",
                JSONObject()
                    .put("height_offset_m", JSONArray(listOf(-0.035, 0.015)))
                    .put("roll_rad", JSONArray(listOf(-0.12, 0.12)))
                    .put("pitch_rad", JSONArray(listOf(-0.12, 0.12)))
                    .put("smoothing_time_s", 0.5)
                    .put("layout", "append_after_history"),
            )
        val builder = PolicyObservationBuilder(PolicyContract.parse(metadata.toString()))
        val first = builder.frame(FloatArray(45), Array(12) { 20 })
        assertEquals(69, first.size)
        assertEquals(6f, first[45], 1.0e-6f)
        assertEquals(1f, first[57], 1.0e-6f)
        val missing = builder.frame(FloatArray(45), arrayOfNulls(12))
        assertEquals(6f, missing[45], 1.0e-6f)
        assertEquals(0f, missing[57], 1.0e-6f)
        val observation = builder.observation(
            Array(4) { missing },
            floatArrayOf(-0.01f, 0.02f, -0.03f),
        )
        assertEquals(279, observation.size)
        assertArrayEquals(
            floatArrayOf(-0.01f, 0.02f, -0.03f),
            observation.takeLast(3).toFloatArray(),
            1.0e-6f,
        )
    }
}
