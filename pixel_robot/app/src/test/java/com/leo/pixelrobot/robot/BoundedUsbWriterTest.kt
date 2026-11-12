package com.leo.pixelrobot.robot

import java.io.IOException
import java.util.ArrayDeque
import java.util.concurrent.Executor
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import org.junit.Assert.*
import org.junit.Test

/** All interleavings are driven explicitly; no USB device, wall-clock sleeps or thread timing. */
class BoundedUsbWriterTest {
    private class ManualExecutor : Executor {
        val pending = ArrayDeque<Runnable>()
        override fun execute(command: Runnable) { pending.addLast(command) }
        fun run() { while (pending.isNotEmpty()) pending.removeFirst().run() }
    }
    private class Clock : UsbDeadlineScheduler {
        class Event(val at: Long, val action: () -> Unit, var cancelled: Boolean = false)
        var now = 0L
        val events = mutableListOf<Event>()
        override fun schedule(delayMs: Long, action: () -> Unit): UsbDeadlineCancellation {
            val event = Event(now + delayMs, action)
            events += event
            return UsbDeadlineCancellation { event.cancelled = true }
        }
        fun advance(ms: Long) {
            now += ms
            while (true) {
                val next = events.firstOrNull { !it.cancelled && it.at <= now } ?: break
                next.cancelled = true
                next.action()
            }
        }
    }
    private class Sink : UsbWriteSink {
        val writes = mutableListOf<String>()
        val timeouts = mutableListOf<Int>()
        var onWrite: (String) -> Unit = {}
        var onClose: () -> Unit = {}
        var closes = 0
        override fun write(bytes: ByteArray, timeoutMs: Int) {
            val text = bytes.toString(Charsets.UTF_8)
            writes += text
            timeouts += timeoutMs
            onWrite(text)
        }
        override fun close() { closes++; onClose() }
    }
    private class Fixture(bytes: Int = 64, records: Int = 4, maxRecord: Int = 32) {
        val worker = ManualExecutor()
        val closer = ManualExecutor()
        val clock = Clock()
        val sink = Sink()
        val errors = mutableListOf<Throwable>()
        val writer = BoundedUsbWriter(sink, worker, closer, clock, "SAFETY\n".toByteArray(),
            onFailure = errors::add, maxQueuedBytes = bytes, maxQueuedRecords = records,
            maxRecordBytes = maxRecord, writeTimeoutMs = 100, safetyDeadlineMs = 500)
        fun send(text: String, generation: UsbWriteGeneration = writer.currentGeneration()) =
            writer.write(text.toByteArray(), generation)
    }
    private fun expectRejected(block: () -> Unit) {
        try { block(); fail("expected rejection") } catch (_: IllegalStateException) { }
    }

    @Test fun normalWritesKeepOrderAndUseBoundedDriverDeadline() {
        val f = Fixture(); f.send("A"); f.send("B"); f.worker.run()
        assertEquals(listOf("A", "B"), f.sink.writes)
        assertEquals(listOf(100, 100), f.sink.timeouts)
    }
    @Test fun enqueueCopiesBytesAgainstMutation() {
        val f = Fixture(); val bytes = "A".toByteArray()
        f.writer.write(bytes); bytes[0] = 'Z'.code.toByte(); f.worker.run()
        assertEquals(listOf("A"), f.sink.writes)
    }
    @Test fun recordCountBackpressureIsExplicitAndBounded() {
        val f = Fixture(records = 2); f.send("A"); f.send("B")
        assertThrows(UsbWriteBackpressureException::class.java) { f.send("C") }
        f.worker.run(); assertEquals(listOf("A", "B"), f.sink.writes)
    }
    @Test fun byteBackpressureIsExplicitAndBounded() {
        val f = Fixture(bytes = 16, maxRecord = 16)
        f.send("123456789"); assertThrows(UsbWriteBackpressureException::class.java) { f.send("12345678") }
        f.send("1234567"); f.worker.run(); assertEquals(16, f.sink.writes.sumOf { it.length })
    }
    @Test fun emptyAndOversizedRecordsAreRejected() {
        val f = Fixture()
        assertThrows(IllegalArgumentException::class.java) { f.send("") }
        assertThrows(IllegalArgumentException::class.java) { f.send("x".repeat(33)) }
        f.worker.run(); assertTrue(f.sink.writes.isEmpty())
    }
    @Test fun stopDiscardsPendingMotionAndReservesSafetyWhenFull() {
        val f = Fixture(records = 2); f.send("M1"); f.send("M2")
        val stop = f.writer.requestSafetyStop(); expectRejected { f.send("M3") }; f.worker.run()
        assertEquals(listOf("SAFETY\n"), f.sink.writes)
        assertEquals(2, stop.join().discardedQueuedRecords)
        assertFalse(stop.join().priorWriteInFlight)
    }
    @Test fun hostSafetyWriteNeverClaimsFirmwareAcknowledgement() {
        val f = Fixture(); val stop = f.writer.requestSafetyStop(); f.worker.run()
        assertTrue(stop.join().safetyWriteCompleted); assertFalse(stop.join().firmwareAcknowledged)
        assertEquals(UsbSafetyWriteOutcome.HOST_WRITE_COMPLETED, stop.join().outcome)
    }
    @Test fun repeatedStopUsesSameBatchAndFuture() {
        val f = Fixture(); val stop = f.writer.requestSafetyStop()
        assertSame(stop, f.writer.requestSafetyStop()); f.worker.run()
        assertSame(stop, f.writer.requestSafetyStop()); f.worker.run()
        assertEquals(listOf("SAFETY\n"), f.sink.writes)
    }
    @Test fun generationChangeDropsQueueAndRejectsOldCapability() {
        val f = Fixture(); val old = f.writer.currentGeneration(); f.send("old", old)
        val next = f.writer.advanceGeneration(); expectRejected { f.send("late", old) }
        f.send("new", next); f.worker.run(); assertEquals(listOf("new"), f.sink.writes)
    }
    @Test fun generationCapabilityCannotCrossConnections() {
        val a = Fixture(); val b = Fixture(); expectRejected { b.send("old", a.writer.currentGeneration()) }
        b.worker.run(); assertTrue(b.sink.writes.isEmpty())
    }
    @Test fun explicitNewGenerationRequiredAfterStopCompletes() {
        val f = Fixture(); f.writer.requestSafetyStop()
        expectRejected { f.writer.advanceGeneration() }; f.worker.run()
        expectRejected { f.send("old") }; f.writer.advanceGeneration(); f.send("new"); f.worker.run()
        assertEquals(listOf("SAFETY\n", "new"), f.sink.writes)
    }
    @Test fun stopDuringInFlightWaitsForItThenDropsAllOtherMotion() {
        val f = Fixture(); f.send("inflight"); f.send("queued")
        f.sink.onWrite = { if (it == "inflight") {
            val report = f.writer.requestSafetyStop(); assertFalse(report.isDone)
            expectRejected { f.send("late") }; assertEquals(listOf("inflight"), f.sink.writes)
        } }
        f.worker.run()
        assertEquals(listOf("inflight", "SAFETY\n"), f.sink.writes)
        assertTrue(f.writer.requestSafetyStop().join().priorWriteInFlight)
    }
    @Test fun closeIsBackgroundAndPortRemainsOpenUntilSafetyAttemptEnds() {
        val f = Fixture(); f.send("queued"); val close = f.writer.closeAsync()
        assertFalse(close.outcome.isDone); assertFalse(close.quiesced.isDone); assertEquals(0, f.sink.closes)
        f.worker.run(); assertTrue(close.outcome.join().safetyWriteCompleted)
        assertEquals(0, f.sink.closes); assertFalse(close.quiesced.isDone)
        f.closer.run(); assertTrue(close.quiesced.isDone); assertEquals(1, f.sink.closes)
    }
    @Test fun repeatedCloseDoesNotDuplicateWritesOrNativeClose() {
        val f = Fixture(); val a = f.writer.closeAsync(); val b = f.writer.closeAsync()
        assertSame(a.outcome, b.outcome); assertSame(a.quiesced, b.quiesced)
        f.worker.run(); f.closer.run(); f.writer.closeAsync(); f.worker.run(); f.closer.run()
        assertEquals(listOf("SAFETY\n"), f.sink.writes); assertEquals(1, f.sink.closes)
    }
    @Test fun nativeWriteTimeoutClosesWithoutSendingSafetyAfterPartialRecord() {
        val f = Fixture(); f.send("partial"); f.send("queued")
        f.sink.onWrite = { f.clock.advance(100) }
        f.worker.run(); val close = f.writer.closeAsync(); f.closer.run()
        assertEquals(listOf("partial"), f.sink.writes)
        assertEquals(UsbSafetyWriteOutcome.WRITE_TIMED_OUT, close.outcome.join().outcome)
        assertFalse(close.outcome.join().safetyWriteCompleted); assertTrue(close.quiesced.isDone)
    }
    @Test fun timedOutNativeWriteMustActuallyReturnBeforeReconnectPermission() {
        val f = Fixture(); f.send("blocked")
        f.sink.onWrite = {
            val close = f.writer.closeAsync()
            f.clock.advance(100); f.closer.run()
            assertTrue(close.outcome.isDone); assertFalse(close.quiesced.isDone)
            assertEquals(1, f.sink.closes)
            expectRejected { f.send("late") }; expectRejected { f.writer.advanceGeneration() }
        }
        f.worker.run()
        assertTrue(f.writer.closeAsync().quiesced.isDone)
        assertEquals(listOf("blocked"), f.sink.writes)
    }
    @Test fun stalledSafetyWriteCannotBecomeSuccessfulAfterDeadline() {
        val f = Fixture(); val close = f.writer.closeAsync()
        f.sink.onWrite = { f.clock.advance(100); assertFalse(close.outcome.join().safetyWriteCompleted) }
        f.worker.run(); f.closer.run()
        assertEquals(UsbSafetyWriteOutcome.WRITE_TIMED_OUT, close.outcome.join().outcome)
        assertTrue(close.quiesced.isDone); assertFalse(close.outcome.join().firmwareAcknowledged)
    }
    @Test fun safetyDeadlineCanExpireBeforeWorkerStarts() {
        val f = Fixture(); val close = f.writer.closeAsync(); f.clock.advance(500)
        assertEquals(UsbSafetyWriteOutcome.SAFETY_DEADLINE_EXCEEDED, close.outcome.join().outcome)
        f.worker.run(); f.closer.run(); assertTrue(f.sink.writes.isEmpty()); assertTrue(close.quiesced.isDone)
    }
    @Test fun writeFailureDoesNotAppendSafetyToUnknownJsonPrefix() {
        val f = Fixture(); f.send("partial"); f.send("queued")
        f.sink.onWrite = { throw IOException("unplug") }; f.worker.run(); f.closer.run()
        assertEquals(listOf("partial"), f.sink.writes)
        val report = f.writer.closeAsync().outcome.join()
        assertEquals(UsbSafetyWriteOutcome.WRITE_FAILED, report.outcome); assertFalse(report.safetyWriteCompleted)
    }
    @Test fun unplugDuringStopRevokesQueuedSafetyAndMotion() {
        val f = Fixture(); f.send("old"); val stop = f.writer.requestSafetyStop()
        f.writer.fail(IOException("USB read failed")); f.worker.run(); f.closer.run()
        assertTrue(f.sink.writes.isEmpty()); assertFalse(stop.join().safetyWriteCompleted)
    }
    @Test fun closeFailureCannotGrantReconnectPermission() {
        val f = Fixture(); f.sink.onClose = { throw IOException("close failed") }
        val close = f.writer.closeAsync(); f.worker.run(); f.closer.run()
        assertTrue(close.quiesced.isCompletedExceptionally)
        assertTrue(close.outcome.join().safetyWriteCompleted); assertFalse(close.outcome.join().firmwareAcknowledged)
    }
    @Test fun authorizationIsCheckedAtDequeueNotJustEnqueue() {
        val f = Fixture(); var authorized = true
        f.writer.write("motion".toByteArray(), withAuthorization = { claim -> if (authorized) claim() })
        authorized = false; f.worker.run()
        assertEquals(listOf("SAFETY\n"), f.sink.writes)
    }
    @Test fun stopDuringAuthorizationPreventsClaimWithoutDeadlock() {
        val f = Fixture()
        f.writer.write("old".toByteArray(), withAuthorization = { claim -> f.writer.requestSafetyStop(); claim() })
        f.worker.run(); assertEquals(listOf("SAFETY\n"), f.sink.writes)
    }
    @Test fun generationChangeDuringAuthorizationPreventsClaim() {
        val f = Fixture()
        f.writer.write("old".toByteArray(), withAuthorization = { claim ->
            f.writer.advanceGeneration(); f.send("new"); claim()
        })
        f.worker.run(); assertEquals(listOf("new"), f.sink.writes)
    }
    @Test fun authorizationExceptionTriggersSafetyWithoutWritingMotion() {
        val f = Fixture()
        f.writer.write("motion".toByteArray(), withAuthorization = { throw IllegalStateException("lease expired") })
        f.worker.run(); assertEquals(listOf("SAFETY\n"), f.sink.writes)
    }
    @Test fun authorizationMustNotThrowAfterClaimOrClaimTwice() {
        val f = Fixture()
        f.writer.write("motion".toByteArray(), withAuthorization = { claim -> claim(); claim() })
        f.worker.run(); assertEquals(listOf("SAFETY\n"), f.sink.writes)
    }
    @Test fun callbacksMayReenterCloseAndNeverRunUnderNativeWriteLock() {
        val f = Fixture(); f.send("bad")
        f.sink.onWrite = { f.writer.fail(IOException("read side loss")); f.writer.closeAsync() }
        f.worker.run(); f.closer.run(); assertEquals(listOf("bad"), f.sink.writes)
        assertTrue(f.writer.closeAsync().quiesced.isDone)
    }
    @Test fun cancelledOldDeadlineCannotKillLaterGeneration() {
        val f = Fixture(); f.send("first"); f.worker.run()
        val oldDeadline = f.clock.events.single().action
        f.writer.advanceGeneration(); f.send("second"); f.worker.run(); oldDeadline()
        f.send("third"); f.worker.run(); assertEquals(listOf("first", "second", "third"), f.sink.writes)
    }
    @Test fun oldSafetyDeadlineCannotKillExplicitlyResumedGeneration() {
        val f = Fixture(); f.writer.requestSafetyStop(); f.worker.run()
        val oldDeadline = f.clock.events.first().action
        f.writer.advanceGeneration(); oldDeadline(); f.send("new"); f.worker.run()
        assertEquals(listOf("SAFETY\n", "new"), f.sink.writes)
    }
    @Test fun oldFailedConnectionCannotEmitOntoReplacementConnection() {
        val old = Fixture(); val fresh = Fixture(); old.send("old-motion")
        old.writer.fail(IOException("unplug")); old.closer.run()
        assertTrue(old.writer.closeAsync().quiesced.isDone)
        fresh.send("hello"); old.worker.run(); fresh.worker.run()
        assertTrue(old.sink.writes.isEmpty()); assertEquals(listOf("hello"), fresh.sink.writes)
    }
    @Test fun selectedUnclaimedRecordStillConsumesQueueBudget() {
        val f = Fixture(records = 1)
        f.writer.write("selected".toByteArray(), withAuthorization = { claim ->
            assertThrows(UsbWriteBackpressureException::class.java) { f.send("overflow") }
            claim()
        })
        f.worker.run(); assertEquals(listOf("selected"), f.sink.writes)
    }
    @Test fun stopCountsSelectedButNotYetClaimedRecordAsDiscarded() {
        val f = Fixture(); var report: UsbSafetyWriteReport? = null
        f.writer.write("selected".toByteArray(), withAuthorization = { claim ->
            f.writer.requestSafetyStop().thenAccept { report = it }; claim()
        })
        f.worker.run(); assertEquals(1, report!!.discardedQueuedRecords)
        assertFalse(report!!.priorWriteInFlight)
    }
    @Test fun retainedClaimCannotEscapeItsSynchronousAuthorityBoundary() {
        val f = Fixture(); var escaped: (() -> Unit)? = null
        f.writer.write("old".toByteArray(), withAuthorization = { escaped = it })
        f.worker.run(); expectRejected { escaped!!.invoke() }
        assertEquals(listOf("SAFETY\n"), f.sink.writes)
    }
    @Test fun rejectingWriterExecutorFailsClosed() {
        val closer = ManualExecutor(); val sink = Sink()
        val writer = BoundedUsbWriter(sink, Executor { throw IllegalStateException("rejected") }, closer,
            Clock(), "SAFETY".toByteArray())
        writer.write("motion".toByteArray()); closer.run()
        assertTrue(sink.writes.isEmpty()); assertTrue(writer.closeAsync().quiesced.isDone)
        assertEquals(UsbSafetyWriteOutcome.WRITE_FAILED, writer.closeAsync().outcome.join().outcome)
    }
    @Test fun rejectingDeadlineSchedulerFailsClosedWithoutStartingNativeWrite() {
        val worker = ManualExecutor(); val closer = ManualExecutor(); val sink = Sink()
        val writer = BoundedUsbWriter(sink, worker, closer,
            UsbDeadlineScheduler { _, _ -> throw IllegalStateException("timer rejected") }, "SAFETY".toByteArray())
        writer.write("motion".toByteArray()); worker.run(); closer.run()
        assertTrue(sink.writes.isEmpty()); assertTrue(writer.closeAsync().quiesced.isDone)
    }
    @Test fun rejectingSafetyDeadlineSchedulerStillClosesAndSettlesOutcome() {
        val worker = ManualExecutor(); val closer = ManualExecutor(); val sink = Sink()
        val writer = BoundedUsbWriter(sink, worker, closer,
            UsbDeadlineScheduler { _, _ -> throw IllegalStateException("timer rejected") }, "SAFETY".toByteArray())
        val result = writer.closeAsync(); worker.run(); closer.run()
        assertFalse(result.outcome.join().safetyWriteCompleted); assertTrue(result.quiesced.isDone)
        assertTrue(sink.writes.isEmpty())
    }
    @Test fun rejectingCloseExecutorCannotGrantReconnectPermission() {
        val worker = ManualExecutor(); val sink = Sink()
        val writer = BoundedUsbWriter(sink, worker, Executor { throw IllegalStateException("close rejected") },
            Clock(), "SAFETY".toByteArray())
        val result = writer.closeAsync(); worker.run()
        assertTrue(result.quiesced.isCompletedExceptionally); assertEquals(0, sink.closes)
    }
    @Test fun blockedNativeThreadHasBoundedOutcomeButNoEarlyQuiescence() {
        val entered = CountDownLatch(1); val release = CountDownLatch(1); val exited = CountDownLatch(1)
        val worker = Executors.newSingleThreadExecutor { Thread(it, "test-usb-blocked").apply { isDaemon = true } }
        val clock = Clock(); val closer = ManualExecutor(); val sink = Sink()
        val writer = BoundedUsbWriter(sink, worker, closer, clock, "SAFETY".toByteArray())
        sink.onWrite = { entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS)) }
        try {
            writer.write("blocked".toByteArray()); assertTrue(entered.await(5, TimeUnit.SECONDS))
            val close = writer.closeAsync(); clock.advance(100); closer.run()
            assertTrue(close.outcome.isDone); assertFalse(close.quiesced.isDone)
            assertEquals(listOf("blocked"), sink.writes)
            close.quiesced.thenRun { exited.countDown() }
            release.countDown(); assertTrue(exited.await(5, TimeUnit.SECONDS))
            assertFalse(close.outcome.join().safetyWriteCompleted)
        } finally { release.countDown(); worker.shutdownNow(); assertTrue(worker.awaitTermination(5, TimeUnit.SECONDS)) }
    }
    @Test fun authorityCallbackNeverHoldsWriterLockAgainstAnotherThreadStop() {
        val f = Fixture(); val failure = AtomicReference<Throwable?>()
        var stopCompletedInsideCallback = false
        var stopThread: Thread? = null
        f.writer.write("motion".toByteArray(), withAuthorization = { claim ->
            val stopped = CountDownLatch(1)
            stopThread = Thread {
                try { f.writer.requestSafetyStop() } catch (error: Throwable) { failure.set(error) }
                finally { stopped.countDown() }
            }.apply { isDaemon = true; start() }
            stopCompletedInsideCallback = stopped.await(5, TimeUnit.SECONDS)
            claim()
        })
        f.worker.run(); stopThread?.join(5_000)
        assertTrue("stop blocked on writer lock during authority callback", stopCompletedInsideCallback)
        assertNull(failure.get()); assertEquals(listOf("SAFETY\n"), f.sink.writes)
    }
    @Test fun actualPortCloseMustReturnBeforeQuiescenceEvenAfterSuccessfulSafetyWrite() {
        val entered = CountDownLatch(1); val release = CountDownLatch(1)
        val worker = ManualExecutor()
        val closer = Executors.newSingleThreadExecutor { Thread(it, "test-usb-close").apply { isDaemon = true } }
        val sink = Sink(); val writer = BoundedUsbWriter(sink, worker, closer, Clock(), "SAFETY".toByteArray())
        sink.onClose = { entered.countDown(); assertTrue(release.await(5, TimeUnit.SECONDS)) }
        try {
            val close = writer.closeAsync(); worker.run(); assertTrue(entered.await(5, TimeUnit.SECONDS))
            assertTrue(close.outcome.join().safetyWriteCompleted); assertFalse(close.quiesced.isDone)
            release.countDown(); close.quiesced.get(5, TimeUnit.SECONDS)
        } finally { release.countDown(); closer.shutdownNow(); assertTrue(closer.awaitTermination(5, TimeUnit.SECONDS)) }
    }

    @Test fun throwingPerWriteDeadlineCancellationCannotStrandInFlightClaim() {
        val worker = ManualExecutor(); val closer = ManualExecutor(); val sink = Sink()
        val writer = BoundedUsbWriter(sink, worker, closer, UsbDeadlineScheduler { _, _ ->
            UsbDeadlineCancellation { throw IllegalStateException("cancel failed") }
        }, "SAFETY".toByteArray())
        writer.write("motion".toByteArray()); worker.run(); closer.run()
        assertTrue(writer.closeAsync().quiesced.isDone)
        assertEquals(UsbSafetyWriteOutcome.WRITE_FAILED, writer.closeAsync().outcome.join().outcome)
        assertEquals(listOf("motion"), sink.writes)
    }
    @Test fun throwingSafetyDeadlineCancellationStillSettlesReportAndCloses() {
        val worker = ManualExecutor(); val closer = ManualExecutor(); val sink = Sink()
        val writer = BoundedUsbWriter(sink, worker, closer, UsbDeadlineScheduler { delay, _ ->
            UsbDeadlineCancellation { if (delay == 500L) throw IllegalStateException("cancel failed") }
        }, "SAFETY".toByteArray())
        val close = writer.closeAsync(); worker.run(); closer.run()
        assertTrue(close.quiesced.isDone); assertEquals(UsbSafetyWriteOutcome.WRITE_FAILED, close.outcome.join().outcome)
        assertEquals(listOf("SAFETY"), sink.writes)
    }

}
