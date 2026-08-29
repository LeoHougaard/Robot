package com.leo.pixelrobot.policy

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class StandTrajectoryTest {
    @Test
    fun synchronizedTrajectoryEasesWithoutExceedingThreeDegreeSteps() {
        val start = (1..12).associateWith { 100f }
        val target = (1..12).associateWith { servoId -> if (servoId == 12) 120f else 110f }
        val plan = StandTrajectory.create(
            start,
            target,
            (1..12).toList(),
            nominalStepDegrees = 1.5f,
            maximumStepDegrees = 3f,
            minimumSteps = 12,
            maximumSteps = 24,
        )

        assertTrue(plan.poses.size in 12..24)
        assertTrue(plan.maximumStepDegrees <= 3.001f)
        assertEquals(target, plan.poses.last())
        val increments = plan.fractions.indices.map { index ->
            plan.fractions[index] - if (index == 0) 0f else plan.fractions[index - 1]
        }
        assertTrue(increments.first() < increments[increments.size / 2])
        assertTrue(increments.last() < increments[increments.size / 2])
    }

    @Test
    fun maximumSupportedMoveKeepsTheExistingSynchronizedSafetyLimit() {
        val start = (1..12).associateWith { 100f }
        val target = (1..12).associateWith { servoId -> if (servoId == 1) 172f else 100f }
        val plan = StandTrajectory.create(
            start,
            target,
            (1..12).toList(),
            nominalStepDegrees = 1.5f,
            maximumStepDegrees = 3f,
            minimumSteps = 12,
            maximumSteps = 24,
        )

        assertEquals(24, plan.poses.size)
        assertEquals(3f, plan.maximumStepDegrees, 1.0e-3f)
        assertEquals(172f, requireNotNull(plan.poses.last()[1]), 1.0e-5f)
    }
}
