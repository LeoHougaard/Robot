package com.leo.pixelrobot.policy

import kotlin.math.abs

/** Builds only actor inputs available on the physical robot. */
class PolicyObservationBuilder(private val contract: PolicyContract) {
    private val heldCurrent = FloatArray(12)

    fun frame(baseFrame: FloatArray, currentRawByPolicyJoint: Array<Int?>): FloatArray {
        require(baseFrame.size == 45 && baseFrame.all(Float::isFinite))
        if (contract.observationBuilder == "v2_180") return baseFrame.copyOf()
        require(currentRawByPolicyJoint.size == 12)
        val current = FloatArray(12)
        val validity = FloatArray(12)
        currentRawByPolicyJoint.indices.forEach { index ->
            val raw = currentRawByPolicyJoint[index]
            if (raw != null) {
                val milliamps = abs(raw.toFloat() * contract.currentStepMilliamps)
                heldCurrent[index] = (
                    (milliamps - contract.currentBiasMilliamps[index]) /
                        contract.currentScaleMilliamps[index]
                    ).coerceIn(0f, contract.currentClipNormalized[index])
                validity[index] = 1f
            }
            current[index] = heldCurrent[index]
        }
        return baseFrame + current + validity
    }

    fun observation(history: Array<FloatArray>, posture: FloatArray): FloatArray {
        require(history.size == contract.observationHistory)
        val flat = history.flatMap { it.asIterable() }.toFloatArray()
        return if (contract.observationBuilder == "current_v3_279") {
            require(posture.size == 3 && posture.all(Float::isFinite))
            flat + posture
        } else {
            flat
        }.also { require(it.size == contract.observationSize) }
    }
}
