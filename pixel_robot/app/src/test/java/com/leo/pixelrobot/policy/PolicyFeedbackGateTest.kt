package com.leo.pixelrobot.policy

import java.io.File
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PolicyFeedbackGateTest {
    @Test
    fun startupSkipsOldTickZeroAndAcceptsFreshNextTick() {
        val tickZero = JSONObject()
            .put("type", "policy_state")
            .put("armed", true)
            .put("tick", 0)
        val tickOne = JSONObject(tickZero.toString()).put("tick", 1)

        assertFalse(isFreshPolicyState(tickZero, previousTick = 0, receivedNs = 0, nowNs = 200_000_000))
        assertTrue(isFreshPolicyState(tickOne, previousTick = 0, receivedNs = 170_000_000, nowNs = 200_000_000))
        assertFalse(isFreshPolicyState(tickOne, previousTick = 0, receivedNs = 0, nowNs = 200_000_000))
    }

    @Test
    fun startupSkipsTickZeroBeforeSensorClockSeesTheLongConfigurationGap() {
        val fixture = JSONObject(requireNotNull(javaClass.getResource("/delivery_sensor_reference.json")).readText())
        val frames = fixture.getJSONArray("frames")
        val bias = fixture.getJSONArray("gyro_bias_dps").floats()
        fun state(frame: Int, sampleMs: Long) =
            JSONObject(frames.getJSONObject(frame).getJSONObject("state").toString())
                .put("sample_ms", sampleMs)

        val tickZero = state(0, 0)
        val delayedTickOne = state(1, 1_289)
        val freshTickOne = state(1, 1_309)
        val freshTickTwo = state(2, 1_329)

        val oldPathSensors = PolicySensors(calibration(), .02f)
        oldPathSensors.read(tickZero, bias)
        assertTrue(runCatching { oldPathSensors.read(delayedTickOne, bias) }.isFailure)

        val startupSensors = PolicySensors(calibration(), .02f)
        assertEquals(.02f, startupSensors.read(freshTickOne, bias).dt, 1e-6f)
        assertEquals(.02f, startupSensors.read(freshTickTwo, bias).dt, 1e-6f)
    }

    private fun calibration() = RobotCalibration.parse(
        File("src/main/assets/assembly-four-leg-linkage-12dof.calibration.json").readText(),
    )
}

private fun org.json.JSONArray.floats() = FloatArray(length()) { getDouble(it).toFloat() }
