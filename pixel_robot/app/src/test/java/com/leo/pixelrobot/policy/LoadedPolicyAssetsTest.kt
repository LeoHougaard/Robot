package com.leo.pixelrobot.policy

import java.io.File
import java.security.MessageDigest
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class LoadedPolicyAssetsTest {
    private val assets = File("src/main/assets")
    private fun bytes(name: String) = File(assets, name).readBytes()
    private fun bundle(calibration: ByteArray = bytes("assembly-four-leg-linkage-12dof.calibration.json")) =
        LoadedPolicyAssets.fromBytes(bytes("policy_metadata.json"), bytes("policy_android_manifest.json"),
            bytes("policy_actor.onnx"), bytes("stride_reference.json"), calibration)

    @Test fun shadowIdentityBindsExactlyTheBytesConsumedByActorAndFrames() {
        val loaded = bundle(); val caps = loaded.shadowCapabilities()
        fun sha(bytes: ByteArray) = MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") { "%02x".format(it) }
        assertEquals(sha(loaded.actor()), caps.assetSha256["policy_actor.onnx"])
        assertEquals(sha(loaded.manifest()), caps.assetSha256["policy_android_manifest.json"])
        assertEquals(sha(loaded.reference()!!), caps.assetSha256["stride_reference.json"])
        assertEquals(sha(loaded.calibrationJson.toByteArray()), caps.assetSha256["calibration"])
        assertEquals(428, loaded.contract.observationSize)
        PolicyFrameSession(loaded.contract, loaded.reference())
    }

    @Test fun inputAndReturnedArraysCannotAlterCapturedIdentity() {
        val metadata = bytes("policy_metadata.json"); val actor = bytes("policy_actor.onnx")
        val loaded = LoadedPolicyAssets.fromBytes(metadata, bytes("policy_android_manifest.json"), actor,
            bytes("stride_reference.json"), bytes("assembly-four-leg-linkage-12dof.calibration.json"))
        val digest = loaded.shadowCapabilities().digest
        metadata.fill(0); actor.fill(0); loaded.actor().fill(0); loaded.manifest().fill(0); loaded.reference()!!.fill(0)
        assertEquals(digest, loaded.shadowCapabilities().digest)
    }

    @Test fun effectiveCalibrationBytesRatherThanBundledFilenameBindGrant() {
        val calibration = bytes("assembly-four-leg-linkage-12dof.calibration.json")
        val differentFormatting = JSONObject(calibration.toString(Charsets.UTF_8)).toString(2).toByteArray()
        val original = bundle().shadowCapabilities(); val effective = bundle(differentFormatting).shadowCapabilities()
        assertNotEquals(original.digest, effective.digest)
        assertNotEquals(original.assetSha256["calibration"], effective.assetSha256["calibration"])
        assertEquals(original.assetSha256["policy_actor.onnx"], effective.assetSha256["policy_actor.onnx"])
    }

    @Test fun parsedContractArraysAreNotSharedAcrossConsumers() {
        val loaded = bundle(); val digest = loaded.shadowCapabilities().digest
        val before = loaded.contract
        val expectedLimits = before.actionLimits.copyOf()
        val expectedScale = before.currentScaleMilliamps.copyOf()
        val expectedHistory = before.selectedHistoryIndices.copyOf()
        before.actionLimits.fill(999f)
        before.currentScaleMilliamps.fill(999f)
        before.selectedHistoryIndices.fill(999)
        assertArrayEquals(expectedLimits, loaded.contract.actionLimits, 0f)
        assertArrayEquals(expectedScale, loaded.contract.currentScaleMilliamps, 0f)
        assertArrayEquals(expectedHistory, loaded.contract.selectedHistoryIndices)
        assertEquals(digest, loaded.shadowCapabilities().digest)
    }

    @Test fun tamperedModelCannotBecomeShadowCompatible() {
        val actor = bytes("policy_actor.onnx"); actor[0] = (actor[0].toInt() xor 1).toByte()
        val loaded = LoadedPolicyAssets.fromBytes(bytes("policy_metadata.json"), bytes("policy_android_manifest.json"), actor,
            bytes("stride_reference.json"), bytes("assembly-four-leg-linkage-12dof.calibration.json"))
        assertTrue(runCatching { loaded.shadowCapabilities() }.isFailure)
    }
}
