package com.leo.pixelrobot.policy

import android.content.res.AssetManager
import org.json.JSONArray
import org.json.JSONObject

data class AppliedActionStep(
    val filtered: FloatArray,
    val applied: FloatArray,
)

class PolicyContract private constructor(value: JSONObject) {
    val profileId: String = value.getString("profile_id")
    val profileSha256: String = value.getString("profile_sha256")
    val weightsSha256: String = value.getString("weights_sha256")
    val commandSmoothingSeconds: Float = value.optDouble("command_smoothing_time_s", 0.4).toFloat()
    val forwardMinimum: Float
    val forwardMaximum: Float
    val lateralMinimum: Float
    val lateralMaximum: Float
    val yawMinimum: Float
    val yawMaximum: Float
    val actorMinimum: Float
    val actorMaximum: Float
    val actionLimits: FloatArray
    val actionFilterAlpha: Float
    val actionSlewLimit: Float
    val positionTargetScaleRadians: Float
    val stationaryPlanarDeadband: Float
    val stationaryYawDeadband: Float
    val stationaryStanceAction: FloatArray
    val stationaryOverrideEnabled: Boolean

    init {
        require(profileId.matches(Regex("[a-z0-9][a-z0-9-]{0,63}"))) { "invalid policy profile id" }
        require(profileSha256.matches(Regex("[A-Fa-f0-9]{64}"))) { "invalid policy profile hash" }
        require(weightsSha256.matches(Regex("[A-Fa-f0-9]{64}"))) { "invalid policy weights hash" }
        require(value.getInt("control_hz") == 50) { "policy control rate must be 50 Hz" }
        require(value.getInt("observation_size") == 180)
        require(value.getInt("observation_history") == 4)
        require(value.getInt("action_size") == ACTION_COUNT)
        require(commandSmoothingSeconds in FRAME_SECONDS..2f)

        val limits = value.getJSONObject("validated_command_limits")
        val forward = limits.getJSONArray("forward_m_s").pair()
        val lateral = limits.getJSONArray("lateral_m_s").pair()
        val yaw = limits.getJSONArray("yaw_rate_rad_s").pair()
        forwardMinimum = forward[0]
        forwardMaximum = forward[1]
        lateralMinimum = lateral[0]
        lateralMaximum = lateral[1]
        yawMinimum = yaw[0]
        yawMaximum = yaw[1]
        require(forwardMinimum <= 0f && forwardMaximum >= 0f)
        require(lateralMinimum <= 0f && lateralMaximum >= 0f)
        require(yawMinimum < 0f && yawMaximum > 0f)

        val action = value.optJSONObject("action_contract")
        if (action == null) {
            val actorClip = value.getJSONArray("action_clip").pair()
            actorMinimum = actorClip[0]
            actorMaximum = actorClip[1]
            actionLimits = FloatArray(ACTION_COUNT) { 1f }
            actionFilterAlpha = 1f
            actionSlewLimit = value.getDouble("action_delta_limit").toFloat()
            positionTargetScaleRadians = value.getDouble("action_scale_rad").toFloat()
            stationaryPlanarDeadband = 0f
            stationaryYawDeadband = 0f
            stationaryStanceAction = FloatArray(ACTION_COUNT)
            stationaryOverrideEnabled = false
        } else {
            val actorClip = action.getJSONArray("actor_output_clip").pair()
            actorMinimum = actorClip[0]
            actorMaximum = actorClip[1]
            actionLimits = action.getJSONArray("applied_normalized_clip_by_joint").floats(ACTION_COUNT)
            actionFilterAlpha = action.getDouble("low_pass_alpha").toFloat()
            actionSlewLimit = action.getDouble("applied_normalized_slew_limit").toFloat()
            positionTargetScaleRadians = action.getDouble("position_target_scale_rad").toFloat()
            val stationary = value.getJSONObject("stationary_action_contract")
            require(stationary.getString("behavior") == "slew_to_validated_four_foot_stance_action")
            stationaryPlanarDeadband = stationary.getDouble("planar_command_deadband_m_s").toFloat()
            stationaryYawDeadband = stationary.getDouble("yaw_command_deadband_rad_s").toFloat()
            stationaryStanceAction = stationary.getJSONArray("normalized_stance_action").floats(ACTION_COUNT)
            stationaryOverrideEnabled = true
        }
        require(actorMinimum == -1f && actorMaximum == 1f)
        require(actionLimits.all { it > 0f && it <= 1f })
        require(actionFilterAlpha > 0f && actionFilterAlpha <= 1f)
        require(actionSlewLimit > 0f && actionSlewLimit <= 1f)
        require(positionTargetScaleRadians > 0f && positionTargetScaleRadians <= 0.5f)
        require(stationaryPlanarDeadband in 0f..0.25f)
        require(stationaryYawDeadband in 0f..0.3f)
        require(stationaryStanceAction.all { it in -1f..1f })
    }

    fun requireRequest(forward: Float, lateral: Float, yawRate: Float) {
        require(forward.isFinite() && forward in forwardMinimum..forwardMaximum)
        require(lateral.isFinite() && lateral in lateralMinimum..lateralMaximum)
        require(yawRate.isFinite() && yawRate in yawMinimum..yawMaximum)
    }

    fun applyAction(
        requested: FloatArray,
        previousFiltered: FloatArray,
        previousApplied: FloatArray,
        command: FloatArray,
    ): AppliedActionStep {
        require(requested.size == ACTION_COUNT && requested.all(Float::isFinite))
        require(previousFiltered.size == ACTION_COUNT && previousFiltered.all(Float::isFinite))
        require(previousApplied.size == ACTION_COUNT && previousApplied.all(Float::isFinite))
        require(command.size == 3 && command.all(Float::isFinite))
        val stationary = stationaryOverrideEnabled &&
            kotlin.math.hypot(command[0], command[1]) <= stationaryPlanarDeadband &&
            kotlin.math.abs(command[2]) <= stationaryYawDeadband
        val filtered = FloatArray(ACTION_COUNT) { index ->
            val bounded = requested[index].coerceIn(-actionLimits[index], actionLimits[index])
            val desired = if (stationary) stationaryStanceAction[index] else bounded
            previousFiltered[index] + actionFilterAlpha * (desired - previousFiltered[index])
        }
        val applied = FloatArray(ACTION_COUNT) { index ->
            filtered[index].coerceIn(
                previousApplied[index] - actionSlewLimit,
                previousApplied[index] + actionSlewLimit,
            )
        }
        return AppliedActionStep(filtered, applied)
    }

    companion object {
        private const val ACTION_COUNT = 12
        private const val FRAME_SECONDS = 0.02f

        fun load(assets: AssetManager): PolicyContract = PolicyContract(
            JSONObject(assets.open("policy_metadata.json").bufferedReader().use { it.readText() }),
        )

        fun parse(json: String): PolicyContract = PolicyContract(JSONObject(json))
    }
}

private fun JSONArray.pair(): FloatArray = floats(2).also { require(it[0] <= it[1]) }

private fun JSONArray.floats(expectedSize: Int): FloatArray {
    require(length() == expectedSize)
    return FloatArray(expectedSize) { index ->
        getDouble(index).toFloat().also { require(it.isFinite()) }
    }
}
