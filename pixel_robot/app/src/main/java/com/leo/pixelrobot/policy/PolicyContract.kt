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
    val controlHz: Int = value.getInt("control_hz")
    val controlFrameSeconds: Float = 1f / controlHz
    val controlFramePeriodNanoseconds: Long = 1_000_000_000L / controlHz
    val commandSmoothingSeconds: Float = value.optDouble("command_smoothing_time_s", 0.4).toFloat()
    val observationSize: Int = value.getInt("observation_size")
    val observationHistory: Int = value.getInt("observation_history")
    val observationBuilder: String = value.optString("observation_builder", "v2_180")
    val selectedHistoryIndices: IntArray
    val timingReferenceMilliseconds: Float
    val currentBiasMilliamps: FloatArray
    val currentScaleMilliamps: FloatArray
    val currentClipNormalized: FloatArray
    val currentStepMilliamps: Float
    val postureHeightMinimum: Float
    val postureHeightMaximum: Float
    val postureRollMinimum: Float
    val postureRollMaximum: Float
    val posturePitchMinimum: Float
    val posturePitchMaximum: Float
    val postureSmoothingSeconds: Float
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
        require(controlHz in 10..100 && 1000 % controlHz == 0) {
            "policy control rate must divide 1000 Hz and be within 10..100 Hz"
        }
        require(observationBuilder in setOf("v2_180", "current_v3_279", "current_body_v14_426"))
        require(
            (observationBuilder == "v2_180" && observationHistory == 4 && observationSize == 180) ||
                (observationBuilder == "current_v3_279" && observationHistory == 4 && observationSize == 279) ||
                (observationBuilder == "current_body_v14_426" && observationHistory == 24 && observationSize == 426)
        ) { "observation builder and size do not match" }
        if (observationBuilder == "current_body_v14_426") {
            val selection = value.getJSONObject("history_selection")
            require(selection.getInt("frame_size") == 70)
            selectedHistoryIndices = selection.getJSONArray("indices").ints(6)
            require(selectedHistoryIndices.contentEquals(intArrayOf(0, 5, 10, 15, 20, 23))) {
                "CurrentBody history selection does not match training"
            }
            timingReferenceMilliseconds = selection.getDouble("timing_reference_ms").toFloat()
            require(timingReferenceMilliseconds > 0f && timingReferenceMilliseconds.isFinite())
        } else {
            selectedHistoryIndices = IntArray(observationHistory) { it }
            timingReferenceMilliseconds = controlFrameSeconds * 1000f
        }
        require(value.getInt("action_size") == ACTION_COUNT)
        require(commandSmoothingSeconds in controlFrameSeconds..2f)

        if (observationBuilder != "v2_180") {
            val current = value.getJSONObject("current_observation_contract")
            require(current.getString("units") == "mA")
            require(current.optBoolean("absolute", false))
            require(current.getString("missing_behavior") == "hold_last_finite_and_validity_zero")
            currentBiasMilliamps = current.getJSONArray("normalization_bias_ma").floats(ACTION_COUNT)
            currentScaleMilliamps = current.getJSONArray("normalization_scale_ma").floats(ACTION_COUNT)
            currentClipNormalized = current.getJSONArray("clip_normalized").floats(ACTION_COUNT)
            currentStepMilliamps = current.getDouble("current_step_ma").toFloat()
            require(currentBiasMilliamps.all { it >= 0f })
            require(currentScaleMilliamps.all { it > 0f })
            require(currentClipNormalized.all { it > 0f })
            require(currentStepMilliamps > 0f)
            val posture = value.getJSONObject("posture_command_contract")
            val height = posture.getJSONArray("height_offset_m").pair()
            val roll = posture.getJSONArray("roll_rad").pair()
            val pitch = posture.getJSONArray("pitch_rad").pair()
            val expectedPostureLayout = if (observationBuilder == "current_body_v14_426") {
                "append_after_selected_history"
            } else {
                "append_after_history"
            }
            require(posture.getString("layout") == expectedPostureLayout)
            postureHeightMinimum = height[0]
            postureHeightMaximum = height[1]
            postureRollMinimum = roll[0]
            postureRollMaximum = roll[1]
            posturePitchMinimum = pitch[0]
            posturePitchMaximum = pitch[1]
            postureSmoothingSeconds = posture.optDouble("smoothing_time_s", 0.5).toFloat()
            require(postureSmoothingSeconds in controlFrameSeconds..2f)
        } else {
            currentBiasMilliamps = FloatArray(ACTION_COUNT)
            currentScaleMilliamps = FloatArray(ACTION_COUNT) { 1f }
            currentClipNormalized = FloatArray(ACTION_COUNT) { 1f }
            currentStepMilliamps = 6.5f
            postureHeightMinimum = 0f
            postureHeightMaximum = 0f
            postureRollMinimum = 0f
            postureRollMaximum = 0f
            posturePitchMinimum = 0f
            posturePitchMaximum = 0f
            postureSmoothingSeconds = 0.5f
        }

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

    fun requirePosture(heightOffset: Float, roll: Float, pitch: Float) {
        require(heightOffset.isFinite() && heightOffset in postureHeightMinimum..postureHeightMaximum)
        require(roll.isFinite() && roll in postureRollMinimum..postureRollMaximum)
        require(pitch.isFinite() && pitch in posturePitchMinimum..posturePitchMaximum)
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

private fun JSONArray.ints(expectedSize: Int): IntArray {
    require(length() == expectedSize)
    return IntArray(expectedSize) { index -> getInt(index) }
}
