package com.leo.pixelrobot.robot

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import org.junit.Assert.*
import org.junit.Test

class ManualOperationGateTest {
    @Test fun stopBeforeDelayedStartPreventsRevival() {
        val gate = ManualOperationGate(); val captured = gate.generation
        gate.invalidate()
        assertTrue(runCatching { gate.admit(captured) { fail("late start revived motion") } }.isFailure)
    }
    @Test fun eachAdmissionIsOneUseEvenWhenStartFails() {
        val gate = ManualOperationGate()
        assertTrue(runCatching { gate.admit(0) { error("not ready") } }.isFailure)
        assertEquals(1L, gate.generation)
        assertTrue(runCatching { gate.admit(0) { fail("replayed failed start") } }.isFailure)
        gate.admit(1) {}
        assertEquals(2L, gate.generation)
    }
    @Test fun expiredRequestRejectedInsideFinalAdmissionLock() {
        val gate = ManualOperationGate()
        assertTrue(runCatching { gate.admit(0, { false }) { fail("expired request") } }.isFailure)
        assertEquals(0L, gate.generation)
    }
    @Test fun startAndConcurrentStopHaveOneOrder() {
        val gate = ManualOperationGate()
        val entered = CountDownLatch(1); val release = CountDownLatch(1); val stopped = CountDownLatch(1)
        val events = java.util.Collections.synchronizedList(mutableListOf<String>())
        val starter = thread {
            gate.admit(0) {
                entered.countDown(); assertTrue(release.await(2, TimeUnit.SECONDS)); events += "started"
            }
        }
        assertTrue(entered.await(1, TimeUnit.SECONDS))
        val stopper = thread { gate.invalidate(); events += "stopped"; stopped.countDown() }
        assertFalse(stopped.await(50, TimeUnit.MILLISECONDS))
        release.countDown(); starter.join(1000); stopper.join(1000)
        assertEquals(listOf("started", "stopped"), events)
        assertTrue(runCatching { gate.admit(0) { fail("old start") } }.isFailure)
    }
}
