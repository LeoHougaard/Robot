package com.leo.pixelrobot.policy

import org.json.JSONArray
import org.json.JSONObject

data class PolicySensorFrame(
    val imu: FloatArray,
    val position: FloatArray,
    val velocity: FloatArray,
    val current: Array<Int?>,
    val dt: Float,
)

/** The sampled sensor path used by control and by recorded-data parity checks. */
class PolicySensors(private val calibration: RobotCalibration, private val nominalDt: Float) {
    private val gravity = GravityEstimator()
    private var previousPosition: FloatArray? = null
    private var previousSampleMs: Long? = null

    fun read(state: JSONObject, gyroBiasDps: FloatArray): PolicySensorFrame {
        val sampleMs = state.getLong("sample_ms") and 0xffff_ffffL
        val dt = previousSampleMs?.let { previous ->
            val elapsed = (sampleMs - previous) and 0xffff_ffffL
            require(elapsed in 5..60) { "invalid or stale firmware sample interval: $elapsed ms" }
            elapsed / 1000f
        } ?: nominalDt
        val ids = state.getJSONArray("ids")
        val angles = state.getJSONArray("angles_deg").finiteFloats(12)
        require(ids.length() == 12)
        val byId = (0 until 12).associate { ids.getInt(it) to angles[it] }
        require(byId.size == 12) { "duplicate servo IDs in feedback" }
        val position = calibration.policyPositions(byId)
        val velocity = previousPosition?.let { old -> FloatArray(12) { (position[it] - old[it]) / dt } }
            ?: FloatArray(12)
        val gyro = calibration.bodyGyroRadPerSecond(state.getJSONArray("gyro_dps").finiteFloats(3), gyroBiasDps)
        val acceleration = calibration.bodyAccelerationMg(state.getJSONArray("accel_mg").finiteFloats(3))
        val projectedGravity = gravity.update(acceleration, gyro, calibration.gravitySign, dt)
        val rawCurrent = state.optJSONArray("current_raw")
        val currentById = if (rawCurrent != null && rawCurrent.length() == 12) {
            (0 until 12).associate { ids.getInt(it) to if (rawCurrent.isNull(it)) null else rawCurrent.getInt(it) }
        } else emptyMap()
        val current = Array(12) { currentById[calibration.servoIds[it]] }
        previousPosition = position
        previousSampleMs = sampleMs
        return PolicySensorFrame(gyro + projectedGravity, position, velocity, current, dt)
    }
}

private fun JSONArray.finiteFloats(size: Int): FloatArray {
    require(length() == size)
    return FloatArray(size) { getDouble(it).toFloat().also { value -> require(value.isFinite()) } }
}
