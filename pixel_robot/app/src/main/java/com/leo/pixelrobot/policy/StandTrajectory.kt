package com.leo.pixelrobot.policy

import kotlin.math.PI
import kotlin.math.ceil
import kotlin.math.sin

data class StandTrajectoryPlan(
    val poses: List<Map<Int, Float>>,
    val fractions: FloatArray,
    val maximumDeltaDegrees: Float,
    val maximumStepDegrees: Float,
)

/** Builds synchronized eased steps without allowing any joint to jump past the safety limit. */
object StandTrajectory {
    fun create(
        startByServoId: Map<Int, Float>,
        targetByServoId: Map<Int, Float>,
        servoIds: List<Int>,
        nominalStepDegrees: Float,
        maximumStepDegrees: Float,
        minimumSteps: Int,
        maximumSteps: Int,
    ): StandTrajectoryPlan {
        require(servoIds.isNotEmpty() && servoIds.toSet().size == servoIds.size)
        require(startByServoId.keys.containsAll(servoIds) && targetByServoId.keys.containsAll(servoIds))
        require(nominalStepDegrees > 0f && maximumStepDegrees >= nominalStepDegrees)
        require(minimumSteps >= 1 && maximumSteps >= minimumSteps)

        val maximumDelta = servoIds.maxOf { servoId ->
            kotlin.math.abs(requireNotNull(targetByServoId[servoId]) - requireNotNull(startByServoId[servoId]))
        }
        if (maximumDelta <= 1.0e-6f) {
            return StandTrajectoryPlan(
                poses = listOf(targetByServoId.filterKeys(servoIds::contains)),
                fractions = floatArrayOf(1f),
                maximumDeltaDegrees = maximumDelta,
                maximumStepDegrees = 0f,
            )
        }

        val requiredSafetySteps = ceil(maximumDelta / maximumStepDegrees).toInt()
        require(requiredSafetySteps <= maximumSteps) {
            "stand target is more than ${maximumSteps * maximumStepDegrees} degrees from the measured pose"
        }
        val desiredSteps = ceil(maximumDelta / nominalStepDegrees).toInt()
        val stepCount = maxOf(minimumSteps, requiredSafetySteps, desiredSteps).coerceAtMost(maximumSteps)
        val fractions = easedFractions(stepCount, maximumStepDegrees / maximumDelta)
        val poses = fractions.map { fraction ->
            servoIds.associateWith { servoId ->
                val start = requireNotNull(startByServoId[servoId])
                start + (requireNotNull(targetByServoId[servoId]) - start) * fraction
            }
        }
        var previous = startByServoId
        var largestStep = 0f
        poses.forEach { pose ->
            servoIds.forEach { servoId ->
                largestStep = maxOf(
                    largestStep,
                    kotlin.math.abs(requireNotNull(pose[servoId]) - requireNotNull(previous[servoId])),
                )
            }
            previous = pose
        }
        require(largestStep <= maximumStepDegrees + 1.0e-3f) {
            "stand trajectory step $largestStep exceeds $maximumStepDegrees degrees"
        }
        return StandTrajectoryPlan(poses, fractions, maximumDelta, largestStep)
    }

    private fun easedFractions(stepCount: Int, maximumFractionStep: Float): FloatArray {
        if (stepCount == 1) return floatArrayOf(1f)
        val uniformWeight = 1f / stepCount
        val rawWeights = FloatArray(stepCount) { index ->
            sin(PI * (index + 0.5) / stepCount).toFloat()
        }
        val rawTotal = rawWeights.sum()
        for (index in rawWeights.indices) rawWeights[index] /= rawTotal
        val rawMaximum = rawWeights.max()
        val blend = if (rawMaximum <= uniformWeight) {
            0f
        } else {
            ((maximumFractionStep - uniformWeight) / (rawMaximum - uniformWeight)).coerceIn(0f, 1f)
        }
        val fractions = FloatArray(stepCount)
        var cumulative = 0f
        for (index in rawWeights.indices) {
            val weight = uniformWeight + blend * (rawWeights[index] - uniformWeight)
            cumulative += weight
            fractions[index] = cumulative.coerceAtMost(1f)
        }
        fractions[fractions.lastIndex] = 1f
        return fractions
    }
}
