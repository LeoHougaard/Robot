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
    fun delayedSamplesUseActualDtAndClockErrorsAreRejected() {
        val fixture = fixture()
        val first = fixture.getJSONArray("frames").getJSONObject(0).getJSONObject("state")
        val bias = fixture.getJSONArray("gyro_bias_dps").floats()
        val calibration = calibration()
        val sensors = PolicySensors(calibration, .02f)
        val firstPosition = sensors.read(first, bias).position
        val delayed = JSONObject(first.toString())
            .put("sample_ms", (first.getLong("sample_ms") + 62L) and 0xffff_ffffL)
        val delayedPosition = JSONObject(delayed.toString())
            .put("angles_deg", JSONArray(first.getJSONArray("angles_deg").let { values ->
                DoubleArray(values.length()) { i -> values.getDouble(i) + if (i == 0) 1.0 else 0.0 }.toList()
            }))
        val delayedRead = sensors.read(delayedPosition, bias)
        assertEquals(.062f, delayedRead.dt, 1e-6f)
        assertEquals((delayedRead.position[0] - firstPosition[0]) / .062f, delayedRead.velocity[0], 1e-5f)

        for (gap in listOf(0L, 121L, -1L)) {
            val rejected = PolicySensors(calibration, .02f)
            rejected.read(first, bias)
            val stale = JSONObject(first.toString()).put("sample_ms", (first.getLong("sample_ms") + gap) and 0xffff_ffffL)
            assertTrue("gap $gap", runCatching { rejected.read(stale, bias) }.isFailure)
        }

        val rollover = PolicySensors(calibration, .02f)
        val beforeWrap = JSONObject(first.toString()).put("sample_ms", 0xffff_fff0L)
        val afterWrap = JSONObject(first.toString()).put("sample_ms", 20L)
        rollover.read(beforeWrap, bias)
        assertEquals(.036f, rollover.read(afterWrap, bias).dt, 1e-6f)
    }
}

private fun JSONArray.floats() = FloatArray(length()) { getDouble(it).toFloat() }
