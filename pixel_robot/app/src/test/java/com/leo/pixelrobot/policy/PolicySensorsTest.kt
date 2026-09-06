package com.leo.pixelrobot.policy

import java.io.File
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PolicySensorsTest {
    private fun fixture() = JSONObject(requireNotNull(javaClass.getResource("/delivery_sensor_reference.json")).readText())
    private fun calibration() = RobotCalibration.parse(File("src/main/assets/assembly-four-leg-linkage-12dof.calibration.json").readText())

    @Test
    fun rawSamplesMatchTrainingObservationsAcrossWrapJitterAndCurrentLoss() {
        val fixture = fixture()
        val contract = PolicyContract.parse(fixture.getJSONObject("metadata").toString())
        val sensors = PolicySensors(calibration(), contract.controlFrameSeconds)
        val session = PolicyFrameSession(contract)
        val frames = fixture.getJSONArray("frames")
        repeat(frames.length()) { index ->
            val frame = frames.getJSONObject(index)
            val read = sensors.read(frame.getJSONObject("state"), fixture.getJSONArray("gyro_bias_dps").floats())
            val command = frame.getJSONArray("command").floats()
            val posture = frame.getJSONArray("posture").floats()
            val observation = session.observation(read, command, posture,
                frame.getJSONArray("previous_action").floats())
            assertEquals(frame.getDouble("dt").toFloat(), read.dt, 1e-6f)
            if (frame.has("expected_observation")) {
                assertArrayEquals("raw sensor parity at frame $index", frame.getJSONArray("expected_observation").floats(),
                    observation, 3e-5f)
            }
            session.action(FloatArray(12), command, posture, read.imu.copyOfRange(3, 6))
            session.completeFrame()
        }
        val builder = PolicyObservationBuilder(contract)
        builder.reset()
        val missing = builder.frame(FloatArray(45), arrayOfNulls(12))
        assertTrue(missing.sliceArray(45 until 69).all { it == 0f })
    }

    @Test
    fun duplicateClockAndLongFeedbackGapAreRejected() {
        val fixture = fixture()
        val first = fixture.getJSONArray("frames").getJSONObject(0).getJSONObject("state")
        val bias = fixture.getJSONArray("gyro_bias_dps").floats()
        for (gap in listOf(0L, 80L)) {
            val sensors = PolicySensors(calibration(), .02f)
            sensors.read(first, bias)
            val stale = JSONObject(first.toString()).put("sample_ms", (first.getLong("sample_ms") + gap) and 0xffff_ffffL)
            assertTrue(runCatching { sensors.read(stale, bias) }.isFailure)
        }
    }
}

private fun JSONArray.floats() = FloatArray(length()) { getDouble(it).toFloat() }
