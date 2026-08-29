package com.leo.pixelrobot.robot

import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.nio.file.Files
import java.security.MessageDigest
import java.util.zip.ZipFile

class TrainingCaptureBundleTest {
    @Test
    fun packageContainsRunExactPolicyCalibrationAndHashes() {
        val root = Files.createTempDirectory("training-capture-test").toFile()
        try {
            val actor = byteArrayOf(1, 2, 3, 4)
            val actorHash = actor.sha256()
            val policyManifest = JSONObject().put("onnx_sha256", actorHash)
            val policyMetadata = JSONObject().put("policy", "test")
            val calibration = JSONObject().put("profile_id", "test")
            val run = root.resolve("robot-run-test.jsonl").apply {
                writeText(
                    JSONObject()
                        .put("type", "session_start")
                        .put(
                            "data",
                            JSONObject().put(
                                "context",
                                JSONObject()
                                    .put("policy_android_manifest", policyManifest)
                                    .put("policy_metadata", policyMetadata)
                                    .put("calibration", calibration),
                            ),
                        ).toString() + "\n",
                )
            }
            val bundle = TrainingCaptureBundle(
                directory = root.resolve("exports"),
                bundledFiles = {
                    mapOf(
                        "policy/policy_actor.onnx" to actor,
                        "policy/policy_android_manifest.json" to policyManifest.toString().toByteArray(),
                        "policy/policy_metadata.json" to policyMetadata.toString().toByteArray(),
                        "calibration/test.calibration.json" to calibration.toString().toByteArray(),
                    )
                },
                manifestContext = { JSONObject().put("profile_id", "test") },
            ).create(run)

            assertTrue(bundle.isFile)
            ZipFile(bundle).use { zip ->
                assertArrayEquals(actor, zip.getInputStream(zip.getEntry("policy/policy_actor.onnx")).readBytes())
                val manifest = JSONObject(
                    zip.getInputStream(zip.getEntry("manifest.json")).bufferedReader().readText(),
                )
                assertEquals("run/robot-run-test.jsonl", manifest.getString("run_entry"))
                assertEquals("test", manifest.getJSONObject("context").getString("profile_id"))
                val files = manifest.getJSONArray("files")
                val actorRecord = (0 until files.length())
                    .map { files.getJSONObject(it) }
                    .single { it.getString("path") == "policy/policy_actor.onnx" }
                assertEquals(actor.sha256(), actorRecord.getString("sha256"))
            }
        } finally {
            root.deleteRecursively()
        }
    }

    private fun ByteArray.sha256(): String = MessageDigest.getInstance("SHA-256")
        .digest(this)
        .joinToString("") { "%02x".format(it) }
}
