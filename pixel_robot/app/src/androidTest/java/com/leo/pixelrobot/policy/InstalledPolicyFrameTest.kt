package com.leo.pixelrobot.policy

import android.content.Context
import androidx.test.core.app.ApplicationProvider
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** Exercises the installed 3750 asset chain without opening USB or motor services. */
class InstalledPolicyFrameTest {
    @Test
    fun installed3750RunsContractSensorsOnnxAndStrideForSixtyFrames() {
        val context = ApplicationProvider.getApplicationContext<Context>()
        val assets = context.assets
        val contract = PolicyContract.load(assets)
        assertEquals(3750, contract.checkpointEpoch)
        val calibration = RobotCalibration.load(assets, contract.profileId,
            expectedCoordinateConvention = contract.jointCoordinateConvention)
        contract.requireCalibration(calibration)
        val feedbackIds = listOf(12, 11, 10, 6, 5, 4, 3, 2, 1, 9, 8, 7)
        val neutralTargets = calibration.servoTargets(FloatArray(12))
        val stateTemplate = JSONObject()
            .put("ids", JSONArray(feedbackIds))
            .put("angles_deg", JSONArray(feedbackIds.map { neutralTargets.getValue(it).toDouble() }))
            .put("gyro_dps", JSONArray(listOf(0.0, 0.0, 0.0)))
            .put("accel_mg", JSONArray(listOf(0.0, 0.0, 1000.0)))
            .put("current_raw", JSONArray(List(12) { JSONObject.NULL }))
        val bias = FloatArray(3)
        val sensor = PolicySensors(calibration, contract.controlFrameSeconds)
        val session = PolicyFrameSession(contract, assets.open("stride_reference.json").use { it.readBytes() })
        val policy = OnnxPolicy(assets, contract.profileId, contract.profileSha256,
            contract.weightsSha256, contract.observationSize)
        try {
            session.reset()
            var previous = FloatArray(12)
            repeat(60) { index ->
                val state = JSONObject(stateTemplate.toString()).put("sample_ms", 1000L + index * 20L)
                val frame = sensor.read(state, bias)
                val observation = session.observation(frame, floatArrayOf(.04f, 0f, 0f),
                    FloatArray(3), previous)
                assertEquals(428, observation.size)
                assertTrue(observation.all(Float::isFinite))
                val residual = policy.action(observation)
                assertEquals(12, residual.size)
                assertTrue(residual.all(Float::isFinite))
                val step = session.action(residual, floatArrayOf(.04f, 0f, 0f),
                    FloatArray(3), frame.imu.copyOfRange(3, 6))
                assertTrue(step.filtered.all(Float::isFinite) && step.applied.all(Float::isFinite))
                if (index < 50) assertTrue(step.applied.all { it == 0f })
                previous = step.applied
                session.completeFrame()
            }
            assertEquals(1.2f, session.elapsedSeconds, 1.0e-5f)
        } finally {
            policy.close()
        }
    }
}
