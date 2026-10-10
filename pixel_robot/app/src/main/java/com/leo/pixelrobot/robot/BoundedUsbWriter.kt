package com.leo.pixelrobot.robot

import java.util.ArrayDeque
import java.util.concurrent.CompletableFuture
import java.util.concurrent.Executor
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/** An opaque capability for one writer instance and one command generation. */
class UsbWriteGeneration internal constructor()

/** Returning from write means only that the host driver accepted bytes, never a firmware receipt. */
interface UsbWriteSink {
    fun write(bytes: ByteArray, timeoutMs: Int)
    fun close()
}

fun interface UsbDeadlineCancellation { fun cancel() }
fun interface UsbDeadlineScheduler {
    fun schedule(delayMs: Long, action: () -> Unit): UsbDeadlineCancellation
}

class ExecutorUsbDeadlineScheduler(private val executor: ScheduledExecutorService) : UsbDeadlineScheduler {
    override fun schedule(delayMs: Long, action: () -> Unit): UsbDeadlineCancellation {
        val future = executor.schedule(action, delayMs, TimeUnit.MILLISECONDS)
        return UsbDeadlineCancellation { future.cancel(false) }
    }
}

enum class UsbSafetyWriteOutcome { HOST_WRITE_COMPLETED, WRITE_FAILED, WRITE_TIMED_OUT, SAFETY_DEADLINE_EXCEEDED }

data class UsbSafetyWriteReport(
    val outcome: UsbSafetyWriteOutcome,
    val priorWriteInFlight: Boolean,
    val discardedQueuedRecords: Int,
) {
    val safetyWriteCompleted: Boolean get() = outcome == UsbSafetyWriteOutcome.HOST_WRITE_COMPLETED
    // This writer has no firmware acknowledgement or torque-register readback path.
    val firmwareAcknowledged: Boolean get() = false
}

/**
 * outcome is deadline-bounded host evidence. quiesced is deliberately NOT deadline-bounded:
 * it completes only after the old port actually closes AND its claimed write returns.
 * A new connection must not be opened before quiesced succeeds. A stuck/failed native close
 * therefore leaves reconnect blocked; reporting a timeout cannot make old I/O disappear.
 */
data class UsbTeardown(
    val outcome: CompletableFuture<UsbSafetyWriteReport>,
    val quiesced: CompletableFuture<Unit>,
)

class UsbWriteBackpressureException : IllegalStateException("USB writer queue is full")

/**
 * One application-owned writer, bounded pending storage and a reserved safety batch.
 *
 * The final claim is the linearization point: stop discards every unclaimed record, but
 * cannot recall a write already claimed by the worker. A stop batch runs only AFTER that
 * write returns successfully. A failed/timed-out write may have emitted partial JSON, so
 * this class closes the link rather than appending safety bytes to an unknown prefix.
 * The independent watchdog closes stalled I/O; neither close nor host write completion
 * establishes physical stopping or torque-off.
 *
 * withAuthorization wraps only the final CLAIM, never blocking port I/O. It is entered
 * without the writer lock; authority owners may hold their own lock while invoking claim.
 * Revocation must release the authority lock before calling writer stop/advanceGeneration.
 */
class BoundedUsbWriter(
    private val sink: UsbWriteSink,
    private val writerExecutor: Executor,
    private val closeExecutor: Executor,
    private val deadlines: UsbDeadlineScheduler,
    safetyBytes: ByteArray,
    private val onFailure: (Throwable) -> Unit = {},
    private val maxQueuedBytes: Int = 16 * 1024,
    private val maxQueuedRecords: Int = 64,
    private val maxRecordBytes: Int = 6_144,
    private val writeTimeoutMs: Int = 100,
    private val safetyDeadlineMs: Long = 500,
) {
    private enum class Mode { ACCEPTING, STOPPING, STOPPED, TERMINAL }
    private class Record(
        val bytes: ByteArray,
        val generation: UsbWriteGeneration,
        val withAuthorization: ((() -> Unit) -> Unit),
        val safety: Boolean = false,
    )
    private class Safety(val report: CompletableFuture<UsbSafetyWriteReport>, val prior: Boolean, val discarded: Int) {
        var deadline: UsbDeadlineCancellation? = null
    }
    private val lock = Any()
    private val safetyBytes = safetyBytes.copyOf()
    private val queue = ArrayDeque<Record>()
    private var queuedBytes = 0
    private var generation = UsbWriteGeneration()
    private var mode = Mode.ACCEPTING
    private var workerScheduled = false
    private var selected: Record? = null
    private var inFlight: Record? = null
    private var safety: Safety? = null
    private var closing = false
    private var closeScheduled = false
    private var portClosed = false
    private val quiesced = CompletableFuture<Unit>()

    init {
        require(maxQueuedBytes > 0 && maxQueuedRecords > 0 && maxRecordBytes > 0)
        require(maxRecordBytes <= maxQueuedBytes)
        require(writeTimeoutMs > 0 && safetyDeadlineMs >= writeTimeoutMs)
        require(this.safetyBytes.isNotEmpty() && this.safetyBytes.size <= maxRecordBytes)
    }

    fun currentGeneration(): UsbWriteGeneration = synchronized(lock) { generation }

    /** Explicitly begin a new operation; pending old records are never inherited. */
    fun advanceGeneration(): UsbWriteGeneration = synchronized(lock) {
        check(!closing && mode != Mode.TERMINAL) { "USB writer is closing" }
        check(mode != Mode.STOPPING) { "USB safety write is still pending" }
        clearQueueLocked()
        generation = UsbWriteGeneration()
        mode = Mode.ACCEPTING
        safety = null
        generation
    }

    fun write(
        bytes: ByteArray,
        generation: UsbWriteGeneration = currentGeneration(),
        withAuthorization: ((() -> Unit) -> Unit) = { it() },
    ) {
        require(bytes.isNotEmpty() && bytes.size <= maxRecordBytes) { "USB record size is out of bounds" }
        synchronized(lock) {
            check(mode == Mode.ACCEPTING && !closing) { "USB writer is stopped or closing" }
            check(generation === this.generation) { "USB write generation is stale" }
            if (queue.size + (if (selected == null) 0 else 1) >= maxQueuedRecords || bytes.size > maxQueuedBytes - queuedBytes) {
                throw UsbWriteBackpressureException()
            }
            queue.addLast(Record(bytes.copyOf(), generation, withAuthorization))
            queuedBytes += bytes.size
        }
        scheduleWorker()
    }

    /** Revokes all pending commands, reserves priority for safety, and rejects new writes. */
    fun requestSafetyStop(): CompletableFuture<UsbSafetyWriteReport> {
        val request: Safety
        var timerFailure: Throwable? = null
        synchronized(lock) {
            safety?.let { return it.report }
            request = Safety(CompletableFuture(), inFlight != null, queue.size + (if (selected == null) 0 else 1))
            safety = request
            generation = UsbWriteGeneration()
            clearQueueLocked()
            if (mode == Mode.TERMINAL) {
                request.report.complete(report(request, UsbSafetyWriteOutcome.WRITE_FAILED))
                return request.report
            }
            mode = Mode.STOPPING
            queue.addFirst(Record(safetyBytes, generation, { it() }, safety = true))
            // Safety has a separate reserved slot and byte budget; ordinary queue saturation
            // cannot reject stop. There is at most one outstanding safety batch.
            try {
                request.deadline = deadlines.schedule(safetyDeadlineMs) {
                    terminate(UsbSafetyWriteOutcome.SAFETY_DEADLINE_EXCEEDED,
                        IllegalStateException("USB safety write deadline exceeded"), request)
                }
            } catch (error: Throwable) { timerFailure = error }
        }
        if (timerFailure != null) terminate(UsbSafetyWriteOutcome.WRITE_FAILED, timerFailure!!) else scheduleWorker()
        return request.report
    }

    /** Nonblocking even when the platform writer/close is stuck. */
    fun closeAsync(): UsbTeardown {
        synchronized(lock) { closing = true }
        val result = requestSafetyStop()
        result.whenComplete { _, _ -> closePort() }
        return UsbTeardown(result, quiesced)
    }

    /** A read-side loss invalidates queued motion immediately; delivery is already unknown. */
    fun fail(error: Throwable) = terminate(UsbSafetyWriteOutcome.WRITE_FAILED, error)

    private fun clearQueueLocked() { queue.clear(); selected = null; queuedBytes = 0 }

    private fun scheduleWorker() {
        val start = synchronized(lock) {
            if (workerScheduled || mode == Mode.TERMINAL || queue.isEmpty()) false
            else { workerScheduled = true; true }
        }
        if (start) {
            try { writerExecutor.execute(::drain) }
            catch (error: Throwable) {
                synchronized(lock) { workerScheduled = false }
                terminate(UsbSafetyWriteOutcome.WRITE_FAILED, error)
            }
        }
    }

    private fun drain() {
        while (true) {
            val record = synchronized(lock) {
                if (mode == Mode.TERMINAL || queue.isEmpty()) {
                    workerScheduled = false
                    null
                } else queue.removeFirst().also { selected = it }
            } ?: return
            var claimed = false
            var claimOpen = true
            var guardFailed = false
            try {
                // No writer lock while acquiring the authority/operation locks.
                record.withAuthorization {
                    synchronized(lock) {
                        check(claimOpen) { "USB claim escaped its authorization callback" }
                        check(!claimed) { "USB authorization invoked the claim twice" }
                        if (record.generation === generation && mode != Mode.TERMINAL &&
                            (record.safety || (mode == Mode.ACCEPTING && !closing))) {
                            check(inFlight == null)
                            if (selected === record) {
                                selected = null
                                if (!record.safety) queuedBytes -= record.bytes.size
                            }
                            inFlight = record
                            claimed = true
                        }
                    }
                }
            } catch (_: Throwable) { guardFailed = true }
            finally { synchronized(lock) {
                claimOpen = false
                if (selected === record) {
                    selected = null
                    if (!record.safety) queuedBytes -= record.bytes.size
                }
            } }
            if (guardFailed || !claimed) {
                if (claimed) synchronized(lock) { if (inFlight === record) inFlight = null }
                // A denied final authority check fails safe, without treating it as a
                // successful transmission or leaving a prior motor command active.
                if (guardFailed || (!record.safety && synchronized(lock) { record.generation === generation })) {
                    requestSafetyStop()
                }
                completeQuiescedIfReady()
                continue
            }
            val timeout = try {
                deadlines.schedule(writeTimeoutMs.toLong()) {
                    terminate(UsbSafetyWriteOutcome.WRITE_TIMED_OUT,
                        IllegalStateException("USB write deadline exceeded"), expectedRecord = record)
                }
            } catch (error: Throwable) {
                synchronized(lock) { if (inFlight === record) inFlight = null }
                terminate(UsbSafetyWriteOutcome.WRITE_FAILED, error)
                completeQuiescedIfReady()
                continue
            }
            var error: Throwable? = null
            try {
                // A timeout can win after claim but before the actual native call.
                if (synchronized(lock) { mode != Mode.TERMINAL }) sink.write(record.bytes, writeTimeoutMs)
            } catch (failure: Throwable) { error = failure }
            finally {
                val cancellationError = runCatching { timeout.cancel() }.exceptionOrNull()
                if (error == null && cancellationError != null) error = cancellationError
            }
            val completed = synchronized(lock) {
                if (inFlight === record) inFlight = null
                if (record.safety && mode != Mode.TERMINAL && error == null) {
                    mode = Mode.STOPPED
                    safety
                } else null
            }
            if (error != null) terminate(UsbSafetyWriteOutcome.WRITE_FAILED, error!!)
            completed?.let {
                val cancellationError = runCatching { it.deadline?.cancel() }.exceptionOrNull()
                if (cancellationError == null) it.report.complete(report(it, UsbSafetyWriteOutcome.HOST_WRITE_COMPLETED))
                else terminate(UsbSafetyWriteOutcome.WRITE_FAILED, cancellationError)
            }
            completeQuiescedIfReady()
        }
    }

    private fun terminate(
        outcome: UsbSafetyWriteOutcome, error: Throwable, only: Safety? = null, expectedRecord: Record? = null,
    ) {
        val result = synchronized(lock) {
            if (mode == Mode.TERMINAL || (expectedRecord != null && inFlight !== expectedRecord) ||
                (only != null && (safety !== only || mode == Mode.STOPPED || only.report.isDone))) return
            mode = Mode.TERMINAL
            closing = true
            generation = UsbWriteGeneration()
            val discarded = queue.count { !it.safety } + (if (selected?.safety == false) 1 else 0)
            clearQueueLocked()
            (safety ?: Safety(CompletableFuture(), inFlight != null, discarded).also { safety = it })
        }
        runCatching { result.deadline?.cancel() }
        result.report.complete(report(result, outcome))
        closePort()
        // Callbacks must not execute with the writer lock held.
        runCatching { onFailure(error) }
    }

    private fun closePort() {
        val start = synchronized(lock) {
            if (closeScheduled) false else {
                closeScheduled = true
                closing = true
                mode = Mode.TERMINAL
                generation = UsbWriteGeneration()
                clearQueueLocked()
                true
            }
        }
        if (!start) return
        try {
            closeExecutor.execute {
                try {
                    sink.close()
                    synchronized(lock) { portClosed = true }
                    completeQuiescedIfReady()
                } catch (error: Throwable) {
                    // Failed close is not reconnect permission.
                    quiesced.completeExceptionally(error)
                    runCatching { onFailure(error) }
                }
            }
        } catch (error: Throwable) {
            quiesced.completeExceptionally(error)
            runCatching { onFailure(error) }
        }
    }

    private fun completeQuiescedIfReady() {
        if (synchronized(lock) { portClosed && inFlight == null && mode == Mode.TERMINAL }) quiesced.complete(Unit)
    }

    private fun report(request: Safety, outcome: UsbSafetyWriteOutcome) =
        UsbSafetyWriteReport(outcome, request.prior, request.discarded)
}
