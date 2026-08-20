package com.leo.pixelrobot.policy

import kotlin.math.abs

class GravityEstimator(private val correctionTimeSeconds: Float = 0.25f) {
    private var estimate: FloatArray? = null

    fun update(accelerationBodyMg: FloatArray, gyroBodyRadPerSecond: FloatArray, gravitySign: Float, dt: Float): FloatArray {
        require(accelerationBodyMg.size == 3 && gyroBodyRadPerSecond.size == 3)
        val magnitude = norm(accelerationBodyMg)
        require(magnitude >= 50f) { "accelerometer vector is unusable: $magnitude mg" }
        val accelerationGravity = FloatArray(3) { gravitySign * accelerationBodyMg[it] / magnitude }
        val previous = estimate
        val next = if (previous == null) {
            accelerationGravity
        } else {
            val rotation = cross(previous, gyroBodyRadPerSecond)
            val predicted = FloatArray(3) { previous[it] + dt * rotation[it] }
            normalizeInPlace(predicted)
            val confidence = (1f - abs(magnitude / 1000f - 1f) / 0.35f).coerceAtLeast(0f)
            val correction = (dt / correctionTimeSeconds.coerceAtLeast(dt)).coerceAtMost(1f) * confidence
            FloatArray(3) { (1f - correction) * predicted[it] + correction * accelerationGravity[it] }
        }
        normalizeInPlace(next)
        estimate = next
        return next.copyOf()
    }

    fun reset() {
        estimate = null
    }

    private fun normalizeInPlace(value: FloatArray) {
        val length = norm(value).coerceAtLeast(1.0e-6f)
        for (index in value.indices) value[index] /= length
    }
}

