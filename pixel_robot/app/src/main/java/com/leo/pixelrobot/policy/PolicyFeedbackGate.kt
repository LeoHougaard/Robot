package com.leo.pixelrobot.policy

import org.json.JSONObject

/** Startup/control feedback checks shared with focused JVM tests. */
internal fun isFreshPolicyState(
    message: JSONObject,
    previousTick: Long,
    receivedNs: Long,
    nowNs: Long,
): Boolean {
    require(message.optBoolean("armed", false)) { "firmware reports policy disarmed" }
    return message.optLong("tick", -1) > previousTick &&
        isFreshFeedbackTimestamp(receivedNs, nowNs)
}

internal fun isFreshFeedbackTimestamp(
    receivedNs: Long,
    nowNs: Long,
    maxAgeNs: Long = 40_000_000L,
): Boolean = nowNs >= receivedNs && nowNs - receivedNs <= maxAgeNs
