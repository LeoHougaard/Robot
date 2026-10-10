package com.leo.pixelrobot.robot

import java.io.Closeable
import java.util.ArrayDeque
import java.util.concurrent.CompletableFuture
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** One owner of port writes. Queue acceptance and driver completion are not firmware receipt. */
class BoundedSerialWriter(
    private val writePort: (ByteArray, Int) -> Unit,
    private val closePort: () -> Unit,
    private val onFailure: (Throwable) -> Unit,
    private val clockNs: () -> Long = System::nanoTime,
    private val writeTimeoutMs: Int = 40,
    private val stopTimeoutMs: Long = 150,
    private val maxQueueBytes: Int = 16 * 1024,
) : Closeable {
    enum class StopResult { DRIVER_WRITTEN_RECEIPT_UNKNOWN, FAILED_RECEIPT_UNKNOWN }
    private data class Record(val bytes: ByteArray, val deadline: Long, val generation: Long, val gate: (() -> Unit) -> Unit)
    private val lock = Object()
    private val queue = ArrayDeque<Record>()
    private var queuedBytes = 0
    private var generation = 0L
    private var closed = false
    private var stopping = false
    private var closeAfterStop = false
    private var stopFuture: CompletableFuture<StopResult>? = null
    private var safety = emptyList<ByteArray>()
    private var stopDeadline = 0L
    private val portClosed = AtomicBoolean(false)
    private val timer = Executors.newSingleThreadScheduledExecutor { task ->
        Thread(task, "pixel-usb-deadline").apply { isDaemon = true }
    }
    private val worker = Thread(::runWriter, "pixel-usb-writer").apply { isDaemon = true; start() }

    init { require(writeTimeoutMs in 1..100); require(stopTimeoutMs in 1..1000); require(maxQueueBytes > 0) }

    fun enqueue(bytes: ByteArray, allowWhileStopped: Boolean = false, gate: (() -> Unit) -> Unit = { it() }) {
        require(bytes.isNotEmpty() && bytes.size <= maxQueueBytes)
        synchronized(lock) {
            check(!closed && (!stopping || (allowWhileStopped && stopFuture?.isDone == true))) { "USB writer is stopping or closed" }
            check(queuedBytes + bytes.size <= maxQueueBytes) { "USB writer queue is full" }
            queue.add(Record(bytes.copyOf(), clockNs() + writeTimeoutMs * 1_000_000L, generation, gate))
            queuedBytes += bytes.size
            lock.notifyAll()
        }
    }

    /** Clears old queued work immediately. In-flight I/O must end before safety I/O starts. */
    fun stop(records: List<ByteArray>, close: Boolean = false): CompletableFuture<StopResult> = synchronized(lock) {
        require(records.isNotEmpty() && records.sumOf { it.size } <= maxQueueBytes)
        if (stopping || closed) {
            closeAfterStop = closeAfterStop || close
            if (!closed && close && stopFuture?.isDone == true) {
                closed = true
                lock.notifyAll()
                timer.execute { releasePort(); timer.shutdownNow() }
            }
            return@synchronized stopFuture ?: CompletableFuture.completedFuture(StopResult.FAILED_RECEIPT_UNKNOWN)
        }
        stopping = true
        generation++
        closeAfterStop = close
        queue.clear(); queuedBytes = 0
        safety = records.map { it.copyOf() }
        stopDeadline = clockNs() + stopTimeoutMs * 1_000_000L
        val result = CompletableFuture<StopResult>()
        stopFuture = result
        timer.schedule({
            fail(IllegalStateException("USB safety write deadline exceeded")) { stopFuture === result && !result.isDone }
        }, stopTimeoutMs, TimeUnit.MILLISECONDS)
        lock.notifyAll()
        result
    }

    /** A new explicit controller operation may proceed only after the safety barrier ended. */
    fun resume() = synchronized(lock) {
        check(!closed && stopFuture?.isDone != false) { "USB safety barrier is still pending" }
        stopping = false
        generation++
        stopFuture = null
    }

    private fun runWriter() {
        while (true) {
            val next = synchronized(lock) {
                while (!closed && queue.isEmpty() && (!stopping || safety.isEmpty())) lock.wait()
                if (closed) return
                if (stopping && safety.isNotEmpty()) null else queue.removeFirst().also { queuedBytes -= it.bytes.size }
            }
            try {
                if (next != null) {
                    var enteredDriver = false
                    val active = AtomicBoolean(true)
                    val alarm = timer.schedule({
                        fail(IllegalStateException("USB write deadline exceeded")) { active.get() }
                    }, maxOf(1L, (next.deadline - clockNs()) / 1_000_000L), TimeUnit.MILLISECONDS)
                    try {
                        next.gate {
                            check(clockNs() < next.deadline) { "Queued USB record expired" }
                            // Stop may have invalidated a dequeued record before its operation gate ran.
                            synchronized(lock) {
                                check(!closed && next.generation == generation && (!stopping || stopFuture?.isDone == true)) {
                                    "USB record revoked"
                                }
                            }
                            enteredDriver = true
                            writePort(next.bytes, writeTimeoutMs)
                        }
                    } catch (error: Exception) {
                        // A cancelled operation/expired lease must never reach the driver.
                        // Keep the worker available for the independent safety barrier.
                        if (enteredDriver) throw error
                    } finally { synchronized(lock) { active.set(false) }; alarm.cancel(false) }
                } else {
                    val records = synchronized(lock) { safety.also { safety = emptyList() } }
                    for (bytes in records) {
                        val remaining = synchronized(lock) {
                            check(!closed) { "USB closed before safety write" }
                            ((stopDeadline - clockNs()) / 1_000_000L).toInt()
                        }
                        check(remaining > 0) { "USB safety deadline exceeded" }
                        writePort(bytes, minOf(writeTimeoutMs, remaining))
                    }
                    synchronized(lock) {
                        if (!closed) {
                            stopFuture?.complete(StopResult.DRIVER_WRITTEN_RECEIPT_UNKNOWN)
                            if (closeAfterStop) { closed = true; releasePort(); timer.shutdownNow() }
                        }
                    }
                }
            } catch (error: Throwable) { fail(error); return }
        }
    }

    private fun fail(error: Throwable, shouldFail: () -> Boolean = { true }) {
        val notify = synchronized(lock) {
            if (closed || !shouldFail()) false else {
                closed = true; queue.clear(); queuedBytes = 0; safety = emptyList()
                stopFuture?.complete(StopResult.FAILED_RECEIPT_UNKNOWN)
                lock.notifyAll(); true
            }
        }
        if (notify) { releasePort(); timer.shutdownNow(); runCatching { onFailure(error) } }
    }

    private fun releasePort() { if (portClosed.compareAndSet(false, true)) runCatching(closePort) }
    override fun close() { fail(IllegalStateException("USB writer closed")) }
}
