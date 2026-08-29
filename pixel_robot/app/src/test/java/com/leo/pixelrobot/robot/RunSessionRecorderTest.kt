package com.leo.pixelrobot.robot

import java.nio.file.Files
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

class RunSessionRecorderTest {
    @Test
    fun sessionContainsContextRawTrafficDerivedFramesAndCleanEnd() {
        val directory = Files.createTempDirectory("robot-run-recorder-test").toFile()
        var unixMs = 1_000L
        var monotonicNs = 10_000L
        try {
            val recorder = RunSessionRecorder(
                directory = directory,
                contextProvider = { JSONObject().put("policy", "test-policy") },
                unixTimeMillis = { unixMs++ },
                monotonicNanos = { monotonicNs++ },
            )
            assertTrue(recorder.start("stand").active)
            recorder.recordRobotTx("{\"cmd\":\"read\"}\n".toByteArray())
            recorder.recordRobotRx(JSONObject().put("type", "state").put("sample_ms", 7))
            recorder.recordDerivedFrame(
                JSONObject()
                    .put("observation", listOf(1.0, 2.0))
                    .put(
                        "input_robot_state",
                        JSONObject()
                            .put("ids", listOf(1, 2))
                            .put("current_raw", listOf(4, 17))
                            .put("current_complete", true),
                    ),
            )
            recorder.start("policy")
            val finished = recorder.finish("stopped", "test complete")

            assertFalse(finished.active)
            val file = recorder.latestCompletedFile()
            assertNotNull(file)
            assertTrue(requireNotNull(file).name.endsWith(".jsonl"))
            val records = file.readLines().map(::JSONObject)
            assertEquals("session_start", records.first().getString("type"))
            assertEquals(1, records.first().getJSONObject("data").getInt("schema_version"))
            assertEquals(
                "test-policy",
                records.first().getJSONObject("data").getJSONObject("context").getString("policy"),
            )
            assertTrue(records.any { it.getString("type") == "robot_tx" })
            assertTrue(records.any { it.getString("type") == "robot_rx" })
            assertTrue(records.any { it.getString("type") == "derived_policy_frame" })
            val derived = records.single { it.getString("type") == "derived_policy_frame" }
                .getJSONObject("data")
                .getJSONObject("input_robot_state")
            assertEquals(17, derived.getJSONArray("current_raw").getInt(1))
            assertTrue(derived.getBoolean("current_complete"))
            assertTrue(records.any {
                it.getString("type") == "phase_start" &&
                    it.getJSONObject("data").getString("phase") == "policy"
            })
            assertEquals("session_end", records.last().getString("type"))
            assertEquals("stopped", records.last().getJSONObject("data").getString("outcome"))
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun nextSessionRecoversAFileLeftByProcessDeath() {
        val directory = Files.createTempDirectory("robot-run-recovery-test").toFile()
        try {
            val partial = directory.resolve("robot-run-old.jsonl.partial")
            partial.writeText("{\"type\":\"session_start\"}\n")
            val recorder = RunSessionRecorder(directory, { JSONObject() })

            recorder.start("stand")
            recorder.finish("stopped", "done")

            val recovered = directory.resolve("robot-run-old.interrupted.jsonl")
            assertTrue(recovered.isFile)
            assertEquals("{\"type\":\"session_start\"}\n", recovered.readText())
        } finally {
            directory.deleteRecursively()
        }
    }

    @Test
    fun unavailableRecorderReportsErrorWithoutThrowing() {
        val directory = Files.createTempDirectory("robot-run-best-effort-test").toFile()
        try {
            val recorder = RunSessionRecorder(directory, { error("context unavailable") })

            val status = recorder.start("stand")

            assertFalse(status.active)
            assertEquals("context unavailable", status.error)
        } finally {
            directory.deleteRecursively()
        }
    }
}
