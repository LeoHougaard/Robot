package com.leo.pixelrobot.policy

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.security.MessageDigest
import kotlin.math.abs
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Offline instrumentation packaging/parity check; it never opens USB or writes motor targets. */
@RunWith(AndroidJUnit4::class)
class CandidateEpoch2750AssetSmokeTest {
    @Test
    fun candidateAssetsLoadAndMatchReferenceVectors() {
        val assets = InstrumentationRegistry.getInstrumentation().context.assets
        val prefix = "candidate_epoch2750"
        fun text(name: String) = assets.open("$prefix/$name").bufferedReader().use { it.readText() }
        val metadata = JSONObject(text("policy_metadata.json"))
        val manifest = JSONObject(text("policy_android_manifest.json"))
        val reference = JSONObject(text("stride-reference-cad-20260906.json"))
        val vectors = JSONObject(text("policy_reference_vectors.json"))
        assertEquals(2750, metadata.getInt("checkpoint_epoch"))
        assertEquals("6a126d6", metadata.getString("source_commit"))
        assertEquals("2026-09-06_10-33-19", metadata.getString("source_run"))
        assertEquals(428, metadata.getInt("observation_size"))
        assertEquals("cad_drives_v1", metadata.getString("joint_coordinate_convention"))
        assertEquals(metadata.getString("profile_sha256"), manifest.getString("profile_sha256"))
        assertEquals(metadata.getString("weights_sha256"), manifest.getString("source_weights_sha256"))
        assertEquals(metadata.getString("checkpoint_sha256"), vectors.getString("checkpoint_sha256"))
        assertEquals(metadata.getString("profile_sha256"), reference.getString("profile_sha256"))
        assertEquals(metadata.getString("reference_sha256"), "b2db6928c7aa3d7acb521a5f254250807a371f2eaac75783ff087b0240217fc2")
        val contract = PolicyContract.parse(text("policy_metadata.json"))
        assertEquals(428, contract.observationSize)
        assertEquals("cad_drives_v1", contract.jointCoordinateConvention)
        val calibrationBytes = assets.open("$prefix/assembly-four-leg-linkage-12dof.calibration.json").use { it.readBytes() }
        val calibrationSha = MessageDigest.getInstance("SHA-256").digest(calibrationBytes)
            .joinToString("") { "%02x".format(it.toInt() and 0xff) }
        assertEquals("60fae8876f2df1a7f225b20c9ca542824c1390ea2f9ae7017e348d32f87705b6", calibrationSha)
        val calibration = RobotCalibration.parse(calibrationBytes.toString(Charsets.UTF_8))
        contract.requireCalibration(calibration)
        val referenceBytes = assets.open("$prefix/stride-reference-cad-20260906.json").use { it.readBytes() }
        val session = PolicyFrameSession(contract, referenceBytes)
        session.reset()
        assertEquals(0f, session.elapsedSeconds)
        val cases = vectors.getJSONArray("cases")
        OnnxPolicy(assets, metadata.getString("profile_id"), metadata.getString("profile_sha256"), metadata.getString("weights_sha256"), 428, prefix).use { policy ->
            for (i in 0 until cases.length()) {
                val c = cases.getJSONObject(i)
                val observationJson = c.getJSONArray("observation")
                val expectedJson = c.getJSONArray("action")
                val observation = FloatArray(observationJson.length()) { observationJson.getDouble(it).toFloat() }
                val expected = FloatArray(expectedJson.length()) { expectedJson.getDouble(it).toFloat() }
                val actual = policy.action(observation)
                assertEquals(12, actual.size)
                actual.indices.forEach { j ->
                    assertTrue("reference case $i joint $j", abs(actual[j] - expected[j]) <= 2e-6f + 1e-5f * abs(expected[j]))
                }
            }
        }
    }
}
