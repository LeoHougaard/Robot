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
        val builder = PolicyObservationBuilder(contract)
        var history: Array<FloatArray>? = null
        val frames = fixture.getJSONArray("frames")
        repeat(frames.length()) { index ->
            val frame = frames.getJSONObject(index)
            val read = sensors.read(frame.getJSONObject("state"), fixture.getJSONArray("gyro_bias_dps").floats())
            val base = read.imu + frame.getJSONArray("command").floats() + read.position +
                FloatArray(12) { .05f * read.velocity[it] } + frame.getJSONArray("previous_action").floats()
            val observationFrame = builder.frame(base, read.current, read.dt / .02f)
            history = history?.let { it.drop(1).toTypedArray() + arrayOf(observationFrame) }
                ?: Array(24) { observationFrame.copyOf() }
            assertEquals(frame.getDouble("dt").toFloat(), read.dt, 1e-6f)
            if (frame.has("expected_observation")) {
                assertArrayEquals("raw sensor parity at frame $index", frame.getJSONArray("expected_observation").floats(),
                    builder.observation(requireNotNull(history), frame.getJSONArray("posture").floats()), 3e-5f)
            }
        }
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
