package com.leo.pixelrobot.policy

import org.json.JSONObject

internal fun coalescePolicyAngles(next: JSONObject, previous: JSONObject): JSONObject {
    if (next.optBoolean("feedback_complete", false)) return next
    val nextAngles = next.getJSONArray("angles_deg")
    val previousAngles = previous.getJSONArray("angles_deg")
    require(nextAngles.length() == 12 && previousAngles.length() == 12) {
        "policy feedback must contain 12 servo angles"
    }
    val merged = JSONObject(next.toString())
    val mergedAngles = merged.getJSONArray("angles_deg")
    repeat(12) { index ->
        if (mergedAngles.isNull(index)) mergedAngles.put(index, previousAngles.getDouble(index))
    }
    merged.put("feedback_coalesced", true)
    return merged
}

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

/** Firmware updates seq after accepting a target bus write; its 20 ms feedback
 * scheduler may report the immediately preceding frame while a new frame is in
 * transit. More than one outstanding frame is not accepted. Fresh callback/tick
 * alone cannot turn a frozen applied sequence into progress or motor receipt.
 */
internal fun requirePolicyAcknowledgement(
    issuedSequence: Long,
    acknowledgedSequence: Long,
    knownSequences: Set<Long>,
) {
    require(issuedSequence >= 0 && acknowledgedSequence >= 0 && acknowledgedSequence <= issuedSequence) {
        "policy feedback references an invalid or future command sequence"
    }
    require(issuedSequence - acknowledgedSequence <= 1) {
        "policy acknowledgement is more than one issued frame behind"
    }
    require(acknowledgedSequence in knownSequences) { "policy feedback references an unknown command sequence" }
}
