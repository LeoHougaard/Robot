package com.leo.pixelrobot.policy

import java.security.MessageDigest
import kotlin.math.PI
import kotlin.math.abs
import kotlin.math.cos
import kotlin.math.sin
import kotlin.math.sqrt
import org.json.JSONArray
import org.json.JSONObject

/** V21 reference mathematics. Not connected to policy loading or motor control. */
class StrideReference private constructor(spec: JSONObject) {
    private val feet = spec.getJSONArray("nominal_feet_m").matrix(4, 3)
    private val inverse = spec.getJSONArray("inverse_jacobians").let { array ->
        require(array.length() == 4)
        Array(4) { array.getJSONArray(it).matrix(3, 3) }
    }
    private val offsets = spec.getJSONArray("phase_offsets").vector(4)
    private val frequency = spec.finite("frequency_hz")
    private val angularFrequency = (2.0 * PI * spec.getDouble("frequency_hz")).toFloat()
    private val duty = spec.finite("duty_fraction")
    private val lift = spec.finite("lift_m")
    private val attitudeGain = spec.finite("attitude_gain")
    private val maximumCorrection = spec.finite("maximum_attitude_correction_m")
    private val rampSeconds = spec.finite("ramp_seconds")
    private val settleSeconds = spec.finite("settle_seconds")
    private val residualScale = spec.finite("residual_scale")
    private val positionScale = spec.finite("position_target_scale_rad")

    init {
        require(frequency > 0f && angularFrequency.isFinite() && duty > 0f && duty < 1f)
        require(lift >= 0f && attitudeGain >= 0f && maximumCorrection >= 0f)
        require(rampSeconds > 0f && settleSeconds >= 0f)
        require(residualScale in 0f..1f && positionScale > 0f)
        require(offsets.all { it >= 0f && it < 1f })
    }

    fun clock(elapsedSeconds: Float): FloatArray {
        require(elapsedSeconds.isFinite() && elapsedSeconds >= 0f)
        val angle = angularFrequency * (elapsedSeconds - settleSeconds).coerceAtLeast(0f)
        return floatArrayOf(sin(angle), cos(angle)).also { require(it.all(Float::isFinite)) }
    }

    fun combined(
        residual: FloatArray,
        command: FloatArray,
        posture: FloatArray,
        gravity: FloatArray,
        elapsedSeconds: Float,
    ): FloatArray {
        require(residual.size == 12 && residual.all(Float::isFinite))
        for (vector in arrayOf(command, posture, gravity)) {
            require(vector.size == 3 && vector.all(Float::isFinite))
        }
        require(elapsedSeconds.isFinite() && elapsedSeconds >= 0f)
        val activeTime = (elapsedSeconds - settleSeconds).coerceAtLeast(0f)
        val ramp = (activeTime / rampSeconds).coerceIn(0f, 1f)
        val moving = (sqrt(command[0] * command[0] + command[1] * command[1]) / .04f +
            abs(command[2]) / .2f).coerceIn(0f, 1f)
        val desiredGravity = floatArrayOf(
            -sin(posture[2]), sin(posture[1]) * cos(posture[2]), -cos(posture[1]) * cos(posture[2]),
        )
        val result = FloatArray(12)
        for (leg in 0..3) {
            val nominal = feet[leg]
            val phase = (activeTime * frequency + offsets[leg]) % 1f
            val swing = ((phase - duty) / (1f - duty)).coerceIn(0f, 1f)
            val smooth = swing * swing * (3f - 2f * swing)
            val endpointSlope = swing - 3f * swing * swing + 2f * swing * swing * swing
            fun horizontal(velocity: Float): Float {
                val amplitude = velocity * duty / frequency / 2f
                return if (phase < duty) amplitude * (1f - 2f * phase / duty)
                else -amplitude + 2f * amplitude * smooth -
                    velocity * (1f - duty) / frequency * endpointSlope
            }
            fun plane(normal: FloatArray): Float =
                (-nominal[2] - (normal[0] * nominal[0] + normal[1] * nominal[1])) /
                    normal[2].coerceAtMost(-.5f)
            val correction = ((plane(gravity) - plane(desiredGravity)) * attitudeGain)
                .coerceIn(-maximumCorrection, maximumCorrection)
            val liftSine = sin(PI.toFloat() * swing)
            val z = if (phase < duty) 0f else lift * liftSine * liftSine * moving
            val displacement = floatArrayOf(
                horizontal(command[0] - command[2] * nominal[1]) * ramp,
                horizontal(command[1] + command[2] * nominal[0]) * ramp,
                (z + correction - posture[0]) * ramp,
            )
            for (joint in 0..2) {
                val row = inverse[leg][joint]
                val index = 3 * leg + joint
                result[index] = (row[0] * displacement[0] + row[1] * displacement[1] +
                    row[2] * displacement[2]) / positionScale +
                    residualScale * residual[index].coerceIn(-1f, 1f)
            }
        }
        require(result.all(Float::isFinite))
        return result
    }

    companion object {
        fun parse(bytes: ByteArray, expectedSha256: String, expectedProfileSha256: String): StrideReference {
            val actual = MessageDigest.getInstance("SHA-256").digest(bytes)
                .joinToString("") { "%02x".format(it) }
            require(actual == expectedSha256) { "stride reference hash mismatch" }
            val spec = JSONObject(bytes.toString(Charsets.UTF_8))
            require(spec.getInt("schema_version") == 1 && spec.getString("kind") == "stride_reference_v1")
            require(spec.getString("profile_sha256") == expectedProfileSha256) { "stride profile mismatch" }
            return StrideReference(spec)
        }
    }
}

private fun JSONObject.finite(key: String) = getDouble(key).toFloat().also { require(it.isFinite()) }
private fun JSONArray.vector(size: Int): FloatArray {
    require(length() == size)
    return FloatArray(size) { getDouble(it).toFloat().also { value -> require(value.isFinite()) } }
}
private fun JSONArray.matrix(rows: Int, columns: Int): Array<FloatArray> {
    require(length() == rows)
    return Array(rows) { getJSONArray(it).vector(columns) }
}
