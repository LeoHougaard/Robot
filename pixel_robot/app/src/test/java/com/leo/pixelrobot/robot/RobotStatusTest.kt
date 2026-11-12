package com.leo.pixelrobot.robot

import org.junit.Assert.*
import org.junit.Test

class RobotStatusTest {
    @Test
    fun stopRequestDoesNotFabricateFirmwareDisarmAcknowledgement() {
        val observed = RobotStatus(linkState = LinkState.READY, policyArmed = true,
            lastSequence = 42, feedbackComplete = true, lastMessageAtMs = 1000)
        val requested = observed.withStopRequested()
        assertTrue(requested.policyArmed)
        assertEquals(observed.lastSequence, requested.lastSequence)
        assertEquals(observed.lastMessageAtMs, requested.lastMessageAtMs)
        assertEquals(observed.feedbackComplete, requested.feedbackComplete)
        assertEquals("Stop requested; torque-off receipt unverified", requested.detail)
        assertFalse(requested.detail.contains("sent"))
    }

    @Test
    fun disconnectedStopDoesNotClaimDeliveryOrManufactureFeedback() {
        val disconnected = RobotStatus(linkState = LinkState.ERROR)
        val requested = disconnected.withStopRequested()
        assertEquals(LinkState.ERROR, requested.linkState)
        assertNull(requested.lastMessageAtMs)
        assertNull(requested.lastSequence)
        assertFalse(requested.policyArmed)
        assertEquals(STOP_REQUEST_DETAIL, requested.detail)
    }
}
