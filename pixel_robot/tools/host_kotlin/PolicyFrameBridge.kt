package com.leo.pixelrobot.host

import com.leo.pixelrobot.policy.PolicyContract
import com.leo.pixelrobot.policy.PolicyFrameSession
import com.leo.pixelrobot.policy.PolicySensorFrame
import com.leo.pixelrobot.policy.RobotCalibration
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/** Compute-only JSONL adapter. No Android service, USB, sockets or actuator writes. */
fun main(arguments: Array<String>) {
    try {
        val assets = File(arguments.single())
        val metadataBytes = File(assets, "policy_metadata.json").readBytes()
        val contract = PolicyContract.parse(metadataBytes.toString(Charsets.UTF_8))
        contract.requireLiveActivationCandidate()
        val calibrationBytes = File(assets, "${contract.profileId}.calibration.json").readBytes()
        val referenceBytes = File(assets, "stride_reference.json").readBytes()
        val calibration = RobotCalibration.parse(calibrationBytes.toString(Charsets.UTF_8))
        contract.requireCalibration(calibration)
        val session = PolicyFrameSession(contract, referenceBytes)
        var command: FloatArray? = null
        var posture: FloatArray? = null
        var gravity: FloatArray? = null
        var frame = 0L
        generateSequence(::readLine).forEach { line ->
            require(line.length <= 65_536)
            val input = JSONObject(line)
            val output = JSONObject().put("hardware_access", false).put("frame", frame)
            when (input.getString("op")) {
                "reset" -> {
                    session.reset(); command = null; posture = null; gravity = null; frame = 0
                    output.put("frame", frame).put("observation_size", contract.observationSize)
                        .put("checkpoint_epoch", contract.checkpointEpoch)
                        .put("weights_sha256", contract.weightsSha256)
                        .put("profile_sha256", contract.profileSha256)
                        .put("metadata_sha256", metadataBytes.sha256())
                        .put("stride_reference_sha256", referenceBytes.sha256())
                        .put("calibration_sha256", calibrationBytes.sha256())
                        .put("joint_coordinate_convention", contract.jointCoordinateConvention)
                        .put("elapsed_seconds", session.elapsedSeconds)
                }
                "observe" -> {
                    val c = input.getJSONArray("command").finite(3)
                    val p = input.getJSONArray("posture").finite(3)
                    contract.requireRequest(c[0], c[1], c[2]); contract.requirePosture(p[0], p[1], p[2])
                    val sensor = input.getJSONObject("sensor")
                    val raw = sensor.getJSONArray("current")
                    require(raw.length() == 12)
                    val dt = sensor.getDouble("dt").toFloat()
                    require(dt.isFinite() && dt in 0.005f..0.12f)
                    val s = PolicySensorFrame(sensor.getJSONArray("imu").finite(6),
                        sensor.getJSONArray("position").finite(12), sensor.getJSONArray("velocity").finite(12),
                        Array(12) { if (raw.isNull(it)) null else raw.getInt(it) }, dt)
                    val observation = session.observation(s, c, p, input.getJSONArray("acknowledged_action").finite(12))
                    command = c; posture = p; gravity = s.imu.copyOfRange(3, 6)
                    output.put("observation", JSONArray(observation.toList()))
                        .put("elapsed_seconds", session.elapsedSeconds)
                }
                "action" -> {
                    val c = input.getJSONArray("command").finite(3)
                    val p = input.getJSONArray("posture").finite(3)
                    val g = input.getJSONArray("gravity").finite(3)
                    require(c.contentEquals(requireNotNull(command)) && p.contentEquals(requireNotNull(posture)) && g.contentEquals(requireNotNull(gravity)))
                    val next = session.action(input.getJSONArray("residual").finite(12), c, p, g)
                    val targets = calibration.servoTargets(FloatArray(12) { next.applied[it] * contract.positionTargetScaleRadians })
                    output.put("filtered", JSONArray(next.filtered.toList())).put("applied", JSONArray(next.applied.toList()))
                        .put("servo_ids", JSONArray(calibration.servoIds.toList()))
                        .put("targets_deg", JSONArray(calibration.servoIds.map { targets.getValue(it) }))
                }
                "complete" -> {
                    session.completeFrame(); frame += 1
                    output.put("frame", frame).put("elapsed_seconds", session.elapsedSeconds)
                        .put("acknowledgement", "synthetic_host_only")
                }
                "close" -> { println(output.put("status", "closed")); System.out.flush(); return }
                else -> error("unsupported compute-only operation")
            }
            println(output.put("status", "ok"))
            System.out.flush()
        }
    } catch (error: Throwable) {
        System.err.println("Compute-only policy bridge rejected input (${error.javaClass.simpleName})")
        println(JSONObject().put("status", "failed").put("hardware_access", false)
            .put("error", "invalid compute-only policy frame or bundled contract"))
        kotlin.system.exitProcess(1)
    }
}

private fun ByteArray.sha256(): String = MessageDigest.getInstance("SHA-256")
    .digest(this).joinToString("") { "%02x".format(it) }

private fun JSONArray.finite(size: Int): FloatArray {
    require(length() == size)
    return FloatArray(size) { getDouble(it).toFloat().also { value -> require(value.isFinite()) } }
}
