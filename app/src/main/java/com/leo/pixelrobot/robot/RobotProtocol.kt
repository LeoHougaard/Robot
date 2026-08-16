package com.leo.pixelrobot.robot

import org.json.JSONObject

object RobotProtocol {
    fun command(name: String): ByteArray = line(JSONObject().put("cmd", name))

    fun arm(): ByteArray = line(
        JSONObject()
            .put("cmd", "policy_arm")
            .put("confirm", "CALIBRATED_AND_LIFTED"),
    )

    fun policyFrame(sequence: Long, targetsByServoId: Map<Int, Float>): ByteArray {
        require(targetsByServoId.keys == (1..12).toSet()) { "policy frame requires servo IDs 1 through 12" }
        val targets = JSONObject()
        targetsByServoId.toSortedMap().forEach { (id, value) ->
            require(value.isFinite()) { "servo target must be finite" }
            targets.put(id.toString(), String.format(java.util.Locale.US, "%.4f", value).toDouble())
        }
        return line(JSONObject().put("cmd", "policy_frame").put("seq", sequence).put("targets", targets))
    }

    private fun line(value: JSONObject): ByteArray = (value.toString() + "\n").toByteArray(Charsets.UTF_8)
}

