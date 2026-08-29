package com.leo.pixelrobot.robot

import org.json.JSONObject

enum class LinkState {
    SEARCHING,
    PERMISSION_REQUIRED,
    OPENING,
    HANDSHAKING,
    READY,
    ERROR,
}

object FirmwareCapabilities {
    fun supportsClockedPolicyFeedback(version: String?): Boolean = atLeast(version, 0, 1, 13)

    fun supportsPolicyServoTelemetry(version: String?): Boolean {
        return atLeast(version, 0, 1, 12)
    }

    private fun atLeast(version: String?, major: Int, minor: Int, patch: Int): Boolean {
        if (version == "dev") return true
        val parts = version?.split('.')?.map { component ->
            component.takeWhile(Char::isDigit).toIntOrNull() ?: return false
        } ?: return false
        if (parts.size < 3) return false
        return listOf(parts[0], parts[1], parts[2]) >= listOf(major, minor, patch)
    }

    private operator fun List<Int>.compareTo(other: List<Int>): Int {
        indices.forEach { index ->
            val comparison = this[index].compareTo(other[index])
            if (comparison != 0) return comparison
        }
        return 0
    }
}

data class RobotStatus(
    val linkState: LinkState = LinkState.SEARCHING,
    val detail: String = "Looking for the ESP32",
    val deviceName: String? = null,
    val firmwareVersion: String? = null,
    val policyArmed: Boolean = false,
    val lastSequence: Long? = null,
    val feedbackComplete: Boolean? = null,
    val servoBatteryVoltage: Float? = null,
    val servoBatteryLive: Boolean = false,
    val selectedServoId: Int? = null,
    val servoTelemetry: ServoTelemetry? = null,
    val servoTelemetryById: Map<Int, ServoTelemetry> = emptyMap(),
    val lastMessageAtMs: Long? = null,
)

data class ServoTelemetry(
    val id: Int,
    val name: String,
    val sampleMs: Long,
    val receivedAtMs: Long,
    val positionRaw: Int,
    val positionDegrees: Float,
    val jointAngleDegrees: Float,
    val speedRaw: Int,
    val speedRpm: Float,
    val loadRaw: Int,
    val loadPercent: Float,
    val voltage: Float,
    val temperatureCelsius: Int,
    val asyncWriteFlag: Int,
    val servoStatus: Int,
    val packetStatus: Int,
    val moving: Boolean,
    val currentRaw: Int,
    val currentMilliamps: Float,
    val estimatedTorqueKgCm: Float,
    val estimatedTorqueNm: Float,
    val fullRuntimeFields: Boolean = true,
) {
    fun toJson(): JSONObject = JSONObject()
        .put("id", id)
        .put("name", name)
        .put("sample_ms", sampleMs)
        .put("joint_angle_deg", jointAngleDegrees)
        .put("packet_status", packetStatus)
        .put("current_raw", currentRaw)
        .put("current_ma", currentMilliamps)
        .put("estimated_torque_kg_cm", estimatedTorqueKgCm)
        .put("estimated_torque_nm", estimatedTorqueNm)
        .put("full_runtime_fields", fullRuntimeFields)
        .put("force_calibrated", false)
        .also { json ->
            if (fullRuntimeFields) {
                json.put("position_raw", positionRaw)
                    .put("position_deg", positionDegrees)
                    .put("speed_raw", speedRaw)
                    .put("speed_rpm", speedRpm)
                    .put("load_raw", loadRaw)
                    .put("load_percent", loadPercent)
                    .put("voltage_v", voltage)
                    .put("temperature_c", temperatureCelsius)
                    .put("async_write_flag", asyncWriteFlag)
                    .put("servo_status", servoStatus)
                    .put("moving", moving)
            }
        }

    companion object {
        fun fromJson(message: JSONObject, receivedAtMs: Long): ServoTelemetry {
            require(message.optString("type") == "servo_telemetry")
            val id = message.getInt("id")
            require(id in 1..253)
            return ServoTelemetry(
                id = id,
                name = message.optString("name", "Servo $id"),
                sampleMs = message.getLong("sample_ms"),
                receivedAtMs = receivedAtMs,
                positionRaw = message.getInt("position_raw"),
                positionDegrees = message.finiteFloat("position_deg"),
                jointAngleDegrees = message.finiteFloat("joint_angle_deg"),
                speedRaw = message.getInt("speed_raw"),
                speedRpm = message.finiteFloat("speed_rpm"),
                loadRaw = message.getInt("load_raw"),
                loadPercent = message.finiteFloat("load_percent"),
                voltage = message.finiteFloat("voltage_v"),
                temperatureCelsius = message.getInt("temperature_c"),
                asyncWriteFlag = message.getInt("async_write_flag"),
                servoStatus = message.getInt("servo_status"),
                packetStatus = message.getInt("packet_status"),
                moving = message.getBoolean("moving"),
                currentRaw = message.getInt("current_raw"),
                currentMilliamps = message.finiteFloat("current_ma"),
                estimatedTorqueKgCm = message.finiteFloat("estimated_torque_kg_cm"),
                estimatedTorqueNm = message.finiteFloat("estimated_torque_nm"),
            )
        }

        fun fromPolicyState(message: JSONObject, receivedAtMs: Long): Map<Int, ServoTelemetry> {
            require(message.optString("type") == "policy_state")
            val ids = message.optJSONArray("ids") ?: return emptyMap()
            val jointAngles = message.optJSONArray("angles_deg") ?: return emptyMap()
            val currents = message.optJSONArray("current_raw") ?: return emptyMap()
            val packetStatuses = message.optJSONArray("status_errors") ?: return emptyMap()
            val positions = message.optJSONArray("position_raw")
            val speeds = message.optJSONArray("speed_raw")
            val arrays = listOf(jointAngles, currents, packetStatuses)
            require(ids.length() == 12 && arrays.all { it.length() == ids.length() }) {
                "policy telemetry must contain 12 aligned servo records"
            }
            require(positions == null || positions.length() == ids.length())
            require(speeds == null || speeds.length() == ids.length())
            val sampleMs = message.getLong("sample_ms")
            return buildMap {
                repeat(ids.length()) { index ->
                    if (jointAngles.isNull(index) || currents.isNull(index)) return@repeat
                    val id = ids.getInt(index)
                    require(id in 1..253)
                    val currentRaw = currents.getInt(index)
                    val currentMilliamps = currentRaw * CURRENT_STEP_MA
                    val signedCurrentAmps = currentMilliamps / 1_000f
                    val torqueCurrentAmps = maxOf(0f, kotlin.math.abs(signedCurrentAmps) - NO_LOAD_CURRENT_A)
                    val estimatedTorqueKgCm = kotlin.math.sign(signedCurrentAmps) *
                        torqueCurrentAmps * TORQUE_CONSTANT_KG_CM_PER_A
                    val positionRaw = positions?.takeUnless { it.isNull(index) }?.getInt(index) ?: 0
                    val speedRaw = speeds?.takeUnless { it.isNull(index) }?.getInt(index) ?: 0
                    put(
                        id,
                        ServoTelemetry(
                            id = id,
                            name = "Servo $id",
                            sampleMs = sampleMs,
                            receivedAtMs = receivedAtMs,
                            positionRaw = positionRaw,
                            positionDegrees = positionRaw * FULL_SCALE_DEGREES / POSITION_MAX,
                            jointAngleDegrees = jointAngles.getDouble(index).toFloat(),
                            speedRaw = speedRaw,
                            speedRpm = speedRaw * 60f / 4_096f,
                            loadRaw = 0,
                            loadPercent = 0f,
                            voltage = message.optDouble("servoBatteryVoltage", 0.0).toFloat(),
                            temperatureCelsius = 0,
                            asyncWriteFlag = 0,
                            servoStatus = 0,
                            packetStatus = if (packetStatuses.isNull(index)) 0 else packetStatuses.getInt(index),
                            moving = speedRaw != 0,
                            currentRaw = currentRaw,
                            currentMilliamps = currentMilliamps,
                            estimatedTorqueKgCm = estimatedTorqueKgCm,
                            estimatedTorqueNm = estimatedTorqueKgCm * KG_CM_TO_NM,
                            fullRuntimeFields = false,
                        ),
                    )
                }
            }
        }

        private fun JSONObject.finiteFloat(key: String): Float =
            getDouble(key).also { require(it.isFinite()) { "$key must be finite" } }.toFloat()

        private const val CURRENT_STEP_MA = 6.5f
        private const val NO_LOAD_CURRENT_A = 0.15f
        private const val TORQUE_CONSTANT_KG_CM_PER_A = 7.8f
        private const val KG_CM_TO_NM = 0.0980665f
        private const val FULL_SCALE_DEGREES = 360f
        private const val POSITION_MAX = 4_095f
    }
}

