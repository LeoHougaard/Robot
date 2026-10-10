package com.leo.pixelrobot.policy

import org.junit.Assert.*
import org.junit.Test
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

class PhysicalCommandAuthorityTest {
    private val identity = "a".repeat(64)
    private val motion = MotionRequest(forward = .02f, lateral = .01f, yawRate = .03f)
    @Test fun requiresSeparateOperatorAndHardwareApproval() {
        val authority = PhysicalCommandAuthority { 10L }
        for ((operator, hardware) in listOf(false to false, true to false, false to true))
            assertTrue(runCatching { authority.authorizeOperatorTrial(operator, hardware, identity, motion, 1000) }.isFailure)
    }
    @Test fun namedAxesAndFinalFreshnessArePreserved() {
        var now = 1_000_000L
        val authority = PhysicalCommandAuthority { now }
        val lease = authority.authorizeOperatorTrial(true, true, identity, motion, 1000)
        assertEquals(motion, lease.motion)
        var writes = 0
        authority.write(lease, identity, now) { writes++ }
        now += 40_000_001L
        assertTrue(runCatching { authority.write(lease, identity, 1_000_000L) { writes++ } }.isFailure)
        assertTrue(runCatching { authority.write(lease, identity, now + 1) { writes++ } }.isFailure)
        assertEquals(1, writes)
    }
    @Test fun deadlineReplacementRevocationAndForeignLeaseFailClosed() {
        var now = 0L
        val authority = PhysicalCommandAuthority { now }
        val old = authority.authorizeOperatorTrial(true, true, identity, motion, 1000)
        val lease = authority.authorizeOperatorTrial(true, true, identity, motion, 1000)
        val foreign = PhysicalCommandAuthority { now }.authorizeOperatorTrial(true, true, identity, motion, 1000)
        for (bad in listOf(old, foreign)) assertTrue(runCatching { authority.write(bad, identity, now) { fail() } }.isFailure)
        assertTrue(runCatching { authority.write(lease, "b".repeat(64), now) { fail() } }.isFailure)
        now = 1_000_000_000L
        assertTrue(authority.expired())
        assertTrue(runCatching { authority.write(lease, identity, now) { fail() } }.isFailure)
        authority.revoke()
        assertTrue(runCatching { authority.write(lease, identity, now) { fail() } }.isFailure)
    }
    @Test fun rollbackAndInvalidDurationCannotIssueAuthority() {
        var now = 100L
        val authority = PhysicalCommandAuthority { now }
        val lease = authority.authorizeOperatorTrial(true, true, identity, motion, 1)
        now = 99L
        assertTrue(runCatching { authority.write(lease, identity, null) { fail() } }.isFailure)
        now = 101L
        assertTrue(runCatching { authority.write(lease, identity, null) { fail() } }.isFailure)
        for (duration in listOf(0, -1, 5001))
            assertTrue(runCatching { authority.authorizeOperatorTrial(true, true, identity, motion, duration) }.isFailure)
        assertTrue(runCatching { authority.authorizeOperatorTrial(true, true, identity, motion.copy(forward = Float.NaN), 100) }.isFailure)
        assertTrue(runCatching { authority.authorizeOperatorTrial(true, true, identity, motion.copy(roll = .1f), 100) }.isFailure)
    }
    @Test(timeout = 5000) fun revocationIsOrderedAfterAnAlreadyStartedWrite() {
        val authority = PhysicalCommandAuthority { 0L }
        val lease = authority.authorizeOperatorTrial(true, true, identity, motion, 1000)
        val entered = CountDownLatch(1); val finish = CountDownLatch(1); val revoked = CountDownLatch(1)
        val writer = thread { authority.write(lease, identity, 0L) { entered.countDown(); finish.await() } }
        assertTrue(entered.await(1, TimeUnit.SECONDS))
        val stopper = thread { authority.revoke(); revoked.countDown() }
        assertFalse(revoked.await(20, TimeUnit.MILLISECONDS))
        finish.countDown(); writer.join(); stopper.join()
        assertTrue(runCatching { authority.write(lease, identity, 0L) { fail() } }.isFailure)
    }
}
