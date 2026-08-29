package com.leo.pixelrobot.policy

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class PolicyFeedbackTest {
    @Test
    fun oneIncompleteFrameReusesOnlyTheMissingAngle() {
        val previousAngles = JSONArray((1..12).map(Int::toDouble))
        val nextAngles = JSONArray((1..12).map(Int::toDouble)).put(8, JSONObject.NULL)
        val previous = JSONObject().put("angles_deg", previousAngles)
        val next = JSONObject()
            .put("feedback_complete", false)
            .put("angles_deg", nextAngles)

        val merged = coalescePolicyAngles(next, previous)

        assertFalse(merged.getBoolean("feedback_complete"))
        assertTrue(merged.getBoolean("feedback_coalesced"))
        assertEquals(9.0, merged.getJSONArray("angles_deg").getDouble(8), 0.0)
        assertEquals(12.0, merged.getJSONArray("angles_deg").getDouble(11), 0.0)
    }

    @Test
    fun completeFeedbackPassesThroughUnchanged() {
        val next = JSONObject()
            .put("feedback_complete", true)
            .put("angles_deg", JSONArray((1..12).map(Int::toDouble)))

        assertTrue(coalescePolicyAngles(next, JSONObject()) === next)
    }
}
