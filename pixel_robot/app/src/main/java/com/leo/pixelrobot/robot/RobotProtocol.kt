package com.leo.pixelrobot.robot

import org.json.JSONArray
import org.json.JSONObject

object RobotProtocol {
    fun command(name: String): ByteArray = line(JSONObject().put("cmd", name))

    fun arm(): ByteArray = line(
        JSONObject()
            .put("cmd", "policy_arm")
            .put("confirm", "CALIBRATED_AND_LIFTED")
            .put("compact_feedback", true),
    )

    fun torqueAll(enabled: Boolean): ByteArray = line(
        JSONObject()
            .put("cmd", "servo_torque")
            .put("all", true)
            .put("enabled", enabled),
    )

    fun torqueLimitAll(limit: Int): ByteArray {
        require(limit in 1..1000)
        return line(
            JSONObject()
                .put("cmd", "servo_torque_limit")
                .put("all", true)
                .put("limit", limit),
        )
    }

    fun readAll(): ByteArray = line(
        JSONObject().put("cmd", "read").put("all", true),
    )

    fun configGet(): ByteArray = command("config_get")

    fun imuStatus(): ByteArray = command("imu_status")

    fun configSet(servos: JSONArray): ByteArray {
        require(servos.length() == 12) { "ESP32 calibration requires 12 servo records" }
        return line(
            JSONObject()
                .put("cmd", "config_set")
                .put("servos", JSONArray(servos.toString())),
        ).also { require(it.size < 6_144) { "ESP32 calibration command is too large" } }
    }

    fun servoBusProbe(id: Int, address: Int, length: Int): ByteArray = line(
        JSONObject()
            .put("cmd", "servo_bus_probe")
            .put("id", id)
            .put("address", address)
            .put("length", length),
    )

    fun servoTelemetry(id: Int): ByteArray {
        require(id in 1..12) { "robot servo ID must be from 1 through 12" }
        return line(JSONObject().put("cmd", "servo_telemetry").put("id", id))
    }

    fun policyFrame(sequence: Long, targetsByServoId: Map<Int, Float>): ByteArray {
        require(targetsByServoId.keys == (1..12).toSet()) { "policy frame requires servo IDs 1 through 12" }
        val targets = JSONObject()
        targetsByServoId.toSortedMap().forEach { (id, value) ->
            require(value.isFinite()) { "servo target must be finite" }
            targets.put(id.toString(), String.format(java.util.Locale.US, "%.4f", value).toDouble())
        }
        return line(JSONObject().put("cmd", "policy_frame").put("seq", sequence).put("targets", targets))
    }

    fun playPoseSequence(
        posesByStep: List<Map<Int, Float>>,
        stepMilliseconds: Int,
        speed: Int,
        acceleration: Int,
    ): ByteArray {
        require(posesByStep.isNotEmpty() && posesByStep.size <= MAX_POSE_SEQUENCE_STEPS)
        require(stepMilliseconds in 20..2_000)
        require(speed in 1..4_096)
        require(acceleration in 1..255)
        val steps = org.json.JSONArray()
        posesByStep.forEach { pose ->
            require(pose.keys == (1..12).toSet()) { "stand pose requires servo IDs 1 through 12" }
            val poses = JSONObject()
            pose.toSortedMap().forEach { (id, value) ->
                require(value.isFinite() && value in 0f..360f)
                poses.put(id.toString(), String.format(java.util.Locale.US, "%.4f", value).toDouble())
            }
            steps.put(
                JSONObject()
                    .put("ms", stepMilliseconds)
                    .put("poses", poses)
                    .put("speed", speed)
                    .put("accel", acceleration),
            )
        }
        return line(JSONObject().put("cmd", "play").put("loop", false).put("steps", steps)).also {
            require(it.size < MAX_POSE_SEQUENCE_BYTES) { "stand pose batch is too large" }
        }
    }

    fun programClear(): ByteArray = command("program_clear")

    fun programStep(
        pose: Map<Int, Float>,
        stepMilliseconds: Int,
        speed: Int,
        acceleration: Int,
    ): ByteArray {
        require(pose.keys == (1..12).toSet()) { "stand pose requires servo IDs 1 through 12" }
        require(stepMilliseconds in 20..2_000)
        require(speed in 1..4_096)
        require(acceleration in 1..255)
        val poses = JSONObject()
        pose.toSortedMap().forEach { (id, value) ->
            require(value.isFinite() && value in 0f..360f)
            poses.put(id.toString(), String.format(java.util.Locale.US, "%.4f", value).toDouble())
        }
        return line(
            JSONObject()
                .put("cmd", "program_step")
                .put("ms", stepMilliseconds)
                .put("poses", poses)
                .put("speed", speed)
                .put("accel", acceleration),
        ).also {
            require(it.size < MAX_PROGRAM_STEP_BYTES) { "stand pose step is too large" }
        }
    }

    fun programStart(): ByteArray = line(
        JSONObject()
            .put("cmd", "program_start")
            .put("loop", false),
    )

    private fun line(value: JSONObject): ByteArray = (value.toString() + "\n").toByteArray(Charsets.UTF_8)

    private const val MAX_POSE_SEQUENCE_STEPS = 24
    private const val MAX_POSE_SEQUENCE_BYTES = 6_144
    private const val MAX_PROGRAM_STEP_BYTES = 512
}
