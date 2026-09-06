package com.leo.pixelrobot.policy

import java.io.File
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test

class CandidatePolicyContractTest {
    private val metadataFile = File("src/main/assets/policy_metadata.json")

    @Test
    fun exactCandidatePassesLiveActivationGuard() {
        PolicyContract.parse(metadataFile.readText()).requireLiveActivationCandidate()
    }

    @Test
    fun wrongCheckpointReferenceOrLimitsFailLiveActivationGuard() {
        val original = JSONObject(metadataFile.readText())
        listOf(
            JSONObject(original.toString()).put("checkpoint_epoch", 2749),
            JSONObject(original.toString()).put(
                "stride_reference_contract",
                JSONObject(original.getJSONObject("stride_reference_contract").toString())
                    .put("sha256", "0".repeat(64)),
            ),
            JSONObject(original.toString()).put(
                "validated_command_limits",
                JSONObject(original.getJSONObject("validated_command_limits").toString())
                    .put("forward_m_s", listOf(-0.05, 0.04)),
            ),
        ).forEach { candidate ->
            assertTrue(runCatching {
                PolicyContract.parse(candidate.toString()).requireLiveActivationCandidate()
            }.isFailure)
        }
    }
}
