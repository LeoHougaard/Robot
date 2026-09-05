package com.leo.pixelrobot.policy

import kotlin.math.abs

/** Builds only actor inputs available on the physical robot. */
class PolicyObservationBuilder(private val contract: PolicyContract) {
    private val heldCurrent = FloatArray(12)

    fun reset() { heldCurrent.fill(0f) }

    fun frame(
        baseFrame: FloatArray,
        currentRawByPolicyJoint: Array<Int?>,
        timingRatio: Float = 1f,
    ): FloatArray {
        require(baseFrame.size == 45 && baseFrame.all(Float::isFinite))
        if (contract.observationBuilder == "v2_180") return baseFrame.copyOf()
        require(timingRatio.isFinite() && timingRatio > 0f)
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
        val currentFrame = baseFrame + current + validity
        return if (contract.usesSelectedHistory) {
            currentFrame + timingRatio
        } else {
            currentFrame
        }
    }

    fun observation(history: Array<FloatArray>, posture: FloatArray): FloatArray {
        require(history.size == contract.observationHistory)
        require(history.all { frame -> frame.all(Float::isFinite) })
        require(posture.size == 3 && posture.all(Float::isFinite))
        val observation = when (contract.observationBuilder) {
            "current_v3_279" -> history.flatMap { it.asIterable() }.toFloatArray() + posture
            "current_body_v14_426", "current_body_v20_426" -> {
                val selected = contract.selectedHistoryIndices.flatMap { history[it].asIterable() }.toFloatArray()
                val latestCommand = history.last().copyOfRange(6, 9)
                selected + latestCommand + posture
            }
            else -> history.flatMap { it.asIterable() }.toFloatArray()
        }
        return observation.also { require(it.size == contract.observationSize) }
    }
}
