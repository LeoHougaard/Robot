package com.leo.pixelrobot.robot

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Test

class RobotProtocolTest {
    @Test
    fun armUsesTheFirmwareSafetyConfirmation() {
        val value = JSONObject(RobotProtocol.arm().toString(Charsets.UTF_8))
        assertEquals("policy_arm", value.getString("cmd"))
        assertEquals("CALIBRATED_AND_LIFTED", value.getString("confirm"))
    }

    @Test
    fun policyFrameContainsEveryPhysicalServo() {
        val value = JSONObject(
            RobotProtocol.policyFrame(7, (1..12).associateWith { 170f + it }).toString(Charsets.UTF_8),
        )
        assertEquals(7, value.getLong("seq"))
        assertEquals(
            (1..12).map(Int::toString).toSet(),
            value.getJSONObject("targets").keys().asSequence().toSet(),
        )
    }

    @Test(expected = IllegalArgumentException::class)
    fun policyFrameRejectsMissingServo() {
        RobotProtocol.policyFrame(1, (1..11).associateWith { 180f })
    }
}
