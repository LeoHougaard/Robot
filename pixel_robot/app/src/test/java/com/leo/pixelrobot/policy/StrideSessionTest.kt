package com.leo.pixelrobot.policy

import java.io.File
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class StrideSessionTest {
    private val fixture = JSONObject(File("src/test/resources/stride_session_parity.json").readText())
    private val sensor = JSONObject(File("src/test/resources/delivery_sensor_reference.json").readText())
    private val reference = JSONObject(File("src/test/resources/stride_reference_v1_parity.json").readText())
        .getString("reference_utf8").toByteArray(Charsets.UTF_8)
    private val calibration = RobotCalibration.parse(
        File("src/main/assets/assembly-four-leg-linkage-12dof.calibration.json").readText(),
    )

    @Test
    fun cadDriveSessionMatchesTorchWithoutRepeatingTheKneeTransmission() {
        verifySession(JSONObject(File("src/test/resources/stride_cad_session_parity.json").readText()),
            File("src/test/resources/stride_cad_reference.json").readBytes(),
            RobotCalibration.parse(File("src/test/resources/stride_cad_calibration.json").readText()))
    }

    @Test
    fun minuteOfRawFeedbackMatchesTorchThroughHoldCommandsAcknowledgmentLagAndRestart() {
        verifySession(fixture, reference, calibration)
    }

    private fun verifySession(fixture: JSONObject, reference: ByteArray, calibration: RobotCalibration) {
        val contract = PolicyContract.parse(fixture.getJSONObject("metadata").toString())
        assertEquals(contract.jointCoordinateConvention, calibration.jointCoordinateConvention)
        contract.requireCalibration(calibration)
        val session = StrideSession(contract, reference)
        val builder = PolicyObservationBuilder(contract)
        val expected = fixture.getJSONArray("expected").let { array ->
            (0 until array.length()).associate { array.getJSONObject(it).let { row -> row.getInt("frame") to row } }
        }
        repeat(2) { restart ->
            session.reset()
            builder.reset()
            val sensors = PolicySensors(calibration, contract.controlFrameSeconds)
            var history: Array<FloatArray>? = null
            var command = fixture.getJSONArray("menu").getJSONArray(0).floats()
            var filtered = FloatArray(12)
            var applied = FloatArray(12)
            val acknowledgedActions = mutableMapOf(0 to FloatArray(12))
            var milliseconds = 0xffff_ffffL - 200
            repeat(fixture.getInt("frames")) { index ->
                val template = sensor.getJSONArray("frames").getJSONObject(index % 32).getJSONObject("state")
                val interval = if (index == 0) 20 else intArrayOf(20,19,21)[index % 3]
                milliseconds = (milliseconds + interval) and 0xffff_ffffL
                val state = JSONObject(template.toString()).put("sample_ms", milliseconds)
                val read = sensors.read(state, sensor.getJSONArray("gyro_bias_dps").floats())
                val menu = fixture.getJSONArray("menu")
                val target = menu.getJSONArray((index / 250) % menu.length()).floats()
                command = FloatArray(3) { command[it] + .05f * (target[it] - command[it]) }
                val acknowledged = maxOf(0, index - if (index % 13 == 0) 1 else 0)
                val base = read.imu + command + read.position + FloatArray(12) { .05f * read.velocity[it] } +
                    requireNotNull(acknowledgedActions[acknowledged])
                val frame = builder.frame(base, read.current, read.dt / .02f)
                history = history?.let { it.drop(1).toTypedArray() + arrayOf(frame) }
                    ?: Array(24) { frame.copyOf() }
                val observation = builder.observation(requireNotNull(history), FloatArray(3), session.clock())
                val residual = FloatArray(12) { ((index + 7 * it) % 41 - 20) / 10f }
                val action = session.action(residual, command, FloatArray(3), read.imu.copyOfRange(3,6), filtered, applied)
                filtered = action.filtered
                applied = action.applied
                acknowledgedActions[index + 1] = applied.copyOf()
                val targets = calibration.servoTargets(FloatArray(12) { .3f * applied[it] })
                if (index < 50) {
                    assertArrayEquals(FloatArray(12), applied, 0f)
                    assertArrayEquals(FloatArray(12), filtered, 0f)
                }
                expected[index]?.let { row ->
                    val label = "restart $restart frame $index"
                    assertEquals(label, row.getDouble("elapsed_seconds").toFloat(), session.elapsedSeconds, 0f)
                    assertArrayEquals("$label observation", row.getJSONArray("observation").floats(), observation, 3e-5f)
                    assertArrayEquals("$label filtered", row.getJSONArray("filtered").floats(), filtered, 3e-5f)
                    assertArrayEquals("$label applied", row.getJSONArray("applied").floats(), applied, 3e-5f)
                    assertArrayEquals("$label servo degrees", row.getJSONArray("servo_degrees").floats(),
                        FloatArray(12) { targets.getValue(calibration.servoIds[it]) }, .002f)
                }
                session.advance()
            }
        }
    }

    @Test
    fun cadDriveCalibrationKeepsHipAndKneeMotorCommandsIndependent() {
        val cad = RobotCalibration.parse(File("src/test/resources/stride_cad_calibration.json").readText())
        val metadata = JSONObject(File("src/test/resources/stride_cad_session_parity.json").readText()).getJSONObject("metadata")
        val cadContract = PolicyContract.parse(metadata.toString())
        cadContract.requireCalibration(cad)
        assertTrue(runCatching { cadContract.requireCalibration(calibration) }.isFailure)
        assertTrue(runCatching { PolicyContract.parse(fixture.getJSONObject("metadata").toString()).requireCalibration(cad) }.isFailure)
        for (leg in 0..3) {
            val hip = 3 * leg + 1
            val knee = hip + 1
            val positions = FloatArray(12).also { it[hip] = .1f }
            val targets = cad.servoTargets(positions)
            assertEquals(cad.zeros[knee], targets.getValue(cad.servoIds[knee]), 0f)
            assertArrayEquals(positions, cad.policyPositions(targets), 1e-6f)
        }
        assertTrue(runCatching {
            PolicyContract.parse(JSONObject(metadata.toString()).put("joint_coordinate_convention", "legacy_relative_knee").toString())
        }.isFailure)
        val invalidCalibration = JSONObject(File("src/test/resources/stride_cad_calibration.json").readText())
        invalidCalibration.getJSONArray("joints").getJSONObject(2).put("linkage", JSONObject()
            .put("type", "four_bar_follow").put("parent_policy_index", 1).put("parent_ratio", 1.0))
        assertTrue(runCatching { RobotCalibration.parse(invalidCalibration.toString()) }.isFailure)
    }

    @Test
    fun strideContractRejectsMissingClockReferenceOrChangedActionSemantics() {
        fun metadata() = JSONObject(fixture.getJSONObject("metadata").toString())
        val contract = PolicyContract.parse(metadata().toString())
        val builder = PolicyObservationBuilder(contract)
        assertTrue(runCatching { builder.observation(Array(24) { FloatArray(70) }, FloatArray(3)) }.isFailure)
        for (mutate in listOf<(JSONObject) -> Unit>(
            { it.remove("stride_reference_contract") },
            { it.put("action_semantics", "direct_joint_targets") },
            { it.put("observation_size", 438) },
            { it.getJSONObject("stride_reference_contract").put("initial_hold_frames", 0) },
            { it.getJSONObject("stride_reference_contract").put("clock", "wall_time") },
            { it.getJSONObject("action_contract").put("low_pass_alpha", 1.0) },
            { it.getJSONObject("posture_command_contract").put("roll_rad", JSONArray(listOf(-.1,.1))) },
        )) {
            assertTrue(runCatching { PolicyContract.parse(metadata().also(mutate).toString()) }.isFailure)
        }
        assertTrue(runCatching { StrideSession(contract, reference + ' '.code.toByte()) }.isFailure)
    }
}

private fun JSONArray.floats() = FloatArray(length()) { getDouble(it).toFloat() }
