package com.leo.pixelrobot.robot

import org.junit.Assert.*
import org.junit.Test
import java.util.Collections
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

class BoundedSerialWriterTest {
    private fun safety() = listOf("stop".toByteArray(), "disarm".toByteArray(), "off".toByteArray())
    @Test(timeout = 5000) fun closeAfterAnAlreadyCompletedStopStillClosesThePort() {
        val closed = CountDownLatch(1); val closes = AtomicInteger()
        val writer = BoundedSerialWriter({ _, _ -> }, { closes.incrementAndGet(); closed.countDown() }, {})
        try {
            val result = writer.stop(safety())
            result.get(1, TimeUnit.SECONDS)
            assertSame(result, writer.stop(safety(), close = true))
            assertTrue(closed.await(1, TimeUnit.SECONDS))
            assertTrue(runCatching { writer.resume() }.isFailure)
            assertTrue(runCatching { writer.enqueue("new".toByteArray()) }.isFailure)
        } finally { writer.close() }
        assertEquals(1, closes.get())
    }
    @Test(timeout = 5000) fun stopRevokesADequeuedRecordBeforeItsGateWrites() {
        val entered = CountDownLatch(1); val finish = CountDownLatch(1)
        val writes = Collections.synchronizedList(mutableListOf<String>())
        val writer = BoundedSerialWriter({ bytes, _ -> writes += String(bytes) }, {}, {}, writeTimeoutMs = 100)
        try {
            writer.enqueue("old".toByteArray(), gate = { write -> entered.countDown(); finish.await(); write() })
            assertTrue(entered.await(1, TimeUnit.SECONDS))
            val result = writer.stop(safety())
            finish.countDown()
            result.get(1, TimeUnit.SECONDS)
            assertEquals(listOf("stop", "disarm", "off"), writes)
        } finally { finish.countDown(); writer.close() }
    }
    @Test(timeout = 5000) fun stopDiscardsQueuedMotionAndWaitsForInFlightWrite() {
        val writes = Collections.synchronizedList(mutableListOf<String>())
        val entered = CountDownLatch(1); val finish = CountDownLatch(1); val closes = AtomicInteger()
        val writer = BoundedSerialWriter({ bytes, _ ->
            val value = String(bytes)
            if (value == "flight") { entered.countDown(); assertTrue(finish.await(1, TimeUnit.SECONDS)) }
            writes += value
        }, { closes.incrementAndGet() }, {}, writeTimeoutMs = 100, stopTimeoutMs = 200)
        try {
            writer.enqueue("flight".toByteArray())
            assertTrue(entered.await(1, TimeUnit.SECONDS))
            writer.enqueue("old".toByteArray())
            val result = writer.stop(safety(), close = true)
            assertSame(result, writer.stop(safety(), close = true))
            assertTrue(runCatching { writer.enqueue("new".toByteArray()) }.isFailure)
            assertFalse(result.isDone)
            finish.countDown()
            assertEquals(BoundedSerialWriter.StopResult.DRIVER_WRITTEN_RECEIPT_UNKNOWN, result.get(1, TimeUnit.SECONDS))
            assertEquals(listOf("flight", "stop", "disarm", "off"), writes)
        } finally { finish.countDown(); writer.close() }
        assertEquals(1, closes.get())
    }
    @Test(timeout = 5000) fun stalledWriteClosesWithoutConcurrentSafetyWriteOrLateSuccess() {
        val entered = CountDownLatch(1); val finish = CountDownLatch(1); val exited = CountDownLatch(1)
        val closes = AtomicInteger(); val calls = AtomicInteger(); val closed = CountDownLatch(1)
        val writer = BoundedSerialWriter({ _, _ -> calls.incrementAndGet(); entered.countDown(); finish.await(); exited.countDown() },
            { closes.incrementAndGet(); closed.countDown() }, {}, writeTimeoutMs = 30, stopTimeoutMs = 40)
        try {
            writer.enqueue("flight".toByteArray()); assertTrue(entered.await(1, TimeUnit.SECONDS))
            val result = writer.stop(safety(), close = true)
            assertEquals(BoundedSerialWriter.StopResult.FAILED_RECEIPT_UNKNOWN, result.get(1, TimeUnit.SECONDS))
            finish.countDown(); assertTrue(exited.await(1, TimeUnit.SECONDS))
            assertTrue(closed.await(1, TimeUnit.SECONDS))
            assertEquals(1, calls.get()); assertEquals(1, closes.get())
            assertEquals(BoundedSerialWriter.StopResult.FAILED_RECEIPT_UNKNOWN, result.get())
        } finally { finish.countDown(); writer.close() }
    }
    @Test(timeout = 5000) fun cancelledGateCannotWriteButSafetyBarrierStillRuns() {
        val writes = Collections.synchronizedList(mutableListOf<String>())
        val rejected = CountDownLatch(1)
        val writer = BoundedSerialWriter({ bytes, _ -> writes += String(bytes) }, {}, {})
        try {
            writer.enqueue("old".toByteArray(), gate = { rejected.countDown(); error("revoked operation") })
            assertTrue(rejected.await(1, TimeUnit.SECONDS))
            assertEquals(BoundedSerialWriter.StopResult.DRIVER_WRITTEN_RECEIPT_UNKNOWN,
                writer.stop(safety()).get(1, TimeUnit.SECONDS))
            assertEquals(listOf("stop", "disarm", "off"), writes)
        } finally { writer.close() }
    }
    @Test(timeout = 5000) fun resumeDoesNotInheritAnOldStopTimerAndRecordsAreCopied() {
        val writes = Collections.synchronizedList(mutableListOf<String>())
        val second = CountDownLatch(1)
        val writer = BoundedSerialWriter({ bytes, _ -> writes += String(bytes); if (String(bytes) == "new") second.countDown() }, {}, {})
        try {
            writer.stop(safety()).get(1, TimeUnit.SECONDS)
            assertTrue(runCatching { writer.enqueue("blocked".toByteArray()) }.isFailure)
            writer.resume(); writer.enqueue("new".toByteArray())
            assertTrue(second.await(1, TimeUnit.SECONDS))
            Thread.sleep(180)
            assertEquals(BoundedSerialWriter.StopResult.DRIVER_WRITTEN_RECEIPT_UNKNOWN,
                writer.stop(safety(), close = true).get(1, TimeUnit.SECONDS))
        } finally { writer.close() }
    }
    @Test(timeout = 5000) fun disconnectDuringSafetyWriteReportsUnknownReceipt() {
        val closes = AtomicInteger()
        val writer = BoundedSerialWriter({ _, _ -> error("disconnected") }, { closes.incrementAndGet() }, {})
        try {
            assertEquals(BoundedSerialWriter.StopResult.FAILED_RECEIPT_UNKNOWN,
                writer.stop(safety(), close = true).get(1, TimeUnit.SECONDS))
        } finally { writer.close() }
        assertEquals(1, closes.get())
    }
    @Test(timeout = 5000) fun queueLimitRejectsRatherThanGrowingAndCopiesQueuedBytes() {
        val entered = CountDownLatch(1); val finish = CountDownLatch(1)
        val writes = Collections.synchronizedList(mutableListOf<String>())
        val copied = CountDownLatch(1)
        val writer = BoundedSerialWriter({ bytes, _ ->
            if (String(bytes) == "first") { entered.countDown(); finish.await() }
            writes += String(bytes); if (String(bytes) == "copy") copied.countDown()
        }, {}, {}, writeTimeoutMs = 100, maxQueueBytes = 8)
        try {
            writer.enqueue("first".toByteArray()); assertTrue(entered.await(1, TimeUnit.SECONDS))
            val bytes = "copy".toByteArray(); writer.enqueue(bytes); bytes.fill('x'.code.toByte())
            assertTrue(runCatching { writer.enqueue("12345".toByteArray()) }.isFailure)
            finish.countDown(); assertTrue(copied.await(1, TimeUnit.SECONDS))
            assertEquals(listOf("first", "copy"), writes)
        } finally { finish.countDown(); writer.close() }
    }
}
