package com.leo.pixelrobot.policy

import org.json.JSONObject
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PolicyFeedbackGateTest {
    @Test
    fun startupSkipsOldTickZeroAndAcceptsFreshNextTick() {
        val tickZero = JSONObject()
            .put("type", "policy_state")
            .put("armed", true)
            .put("tick", 0)
        val tickOne = JSONObject(tickZero.toString()).put("tick", 1)

        assertFalse(isFreshPolicyState(tickZero, previousTick = 0, receivedNs = 0, nowNs = 200_000_000))
        assertTrue(isFreshPolicyState(tickOne, previousTick = 0, receivedNs = 170_000_000, nowNs = 200_000_000))
        assertFalse(isFreshPolicyState(tickOne, previousTick = 0, receivedNs = 0, nowNs = 200_000_000))
    }
}
