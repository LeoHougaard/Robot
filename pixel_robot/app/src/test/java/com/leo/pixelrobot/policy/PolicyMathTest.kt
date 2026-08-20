package com.leo.pixelrobot.policy

import java.io.File
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Test

class PolicyMathTest {
    private val calibration by lazy {
        RobotCalibration.parse(File("src/main/assets/assembly-1-12dof.calibration.json").readText())
    }

    @Test
    fun zeroPolicyPoseMapsToMeasuredCenters() {
        val targets = calibration.servoTargets(FloatArray(12))
        val reconstructed = calibration.policyPositions(targets)
        assertArrayEquals(FloatArray(12), reconstructed, 1.0e-6f)
    }

    @Test
    fun fourBarKneeRoundTripsWithFemurMotion() {
        val pose = floatArrayOf(0f, 0.1f, -0.2f, 0f, -0.1f, 0.2f, 0f, 0.1f, -0.2f, 0f, -0.1f, 0.2f)
        val reconstructed = calibration.policyPositions(calibration.servoTargets(pose))
        assertArrayEquals(pose, reconstructed, 1.0e-5f)
    }

    @Test
    fun stationaryAccelerationInitializesProjectedGravity() {
        val estimator = GravityEstimator()
        val value = estimator.update(floatArrayOf(0f, 0f, 1000f), FloatArray(3), -1f, 0.02f)
        assertArrayEquals(floatArrayOf(0f, 0f, -1f), value, 1.0e-6f)
        assertEquals(1f, norm(value), 1.0e-6f)
    }
}

