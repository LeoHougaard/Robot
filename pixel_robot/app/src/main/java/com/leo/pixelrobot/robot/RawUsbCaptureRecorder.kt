package com.leo.pixelrobot.robot

import org.json.JSONObject
import java.io.BufferedWriter
import java.io.File
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Callable
import java.util.concurrent.FutureTask
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong
import java.util.concurrent.atomic.AtomicReference

/** Offline-consumable evidence only. No transport/control/device API is accessible here. */
data class RawUsbCaptureStatus(val file: File, val complete: Boolean, val droppedRecords: Long, val error: String?)

/**
 * Bounded best-effort byte capture. Callback producers only copy a bounded byte array
 * and enqueue; JSON/hex serialization and storage run on a daemon worker. Overflow,
 * I/O failure, size cap, and finalization timeout invalidate evidence rather than
 * block control or silently discard records. An incomplete file is never acceptance.
 *
 * [synthetic] is mandatory: tests must not produce operator-origin evidence.
 * TX is the native driver's attempted bytes, never proof of USB/device delivery.
 */
class RawUsbCaptureRecorder(
    private val file: File,
    synthetic: Boolean,
    context: JSONObject,
    private val clock: () -> Long = System::nanoTime,
    queueCapacity: Int = 1024,
    private val maxChunkBytes: Int = 65_536,
    private val maxCaptureBytes: Long = 64L * 1024 * 1024,
    openWriter: (File) -> BufferedWriter = { it.bufferedWriter(Charsets.UTF_8) },
) {
    private val partialFile = File(file.path + ".partial")
    private fun status() = RawUsbCaptureStatus(if (file.isFile) file else partialFile,
        error.get() == null && dropped.get() == 0L, dropped.get(), error.get())
    private val lock = Any()
    private val sessionId = UUID.randomUUID().toString()
    private val error = AtomicReference<String?>()
    private val dropped = AtomicLong()
    private val maxPendingRecords = queueCapacity
    private val executor = ThreadPoolExecutor(1, 1, 0L, TimeUnit.MILLISECONDS,
        ArrayBlockingQueue<Runnable>(queueCapacity + 1),
        { runnable -> Thread(runnable, "robot-raw-usb-capture").apply { isDaemon = true } },
        ThreadPoolExecutor.AbortPolicy())
    private val writer: BufferedWriter
    private var recordIndex = 0L // worker owned after construction
    private var writtenBytes = 0L
    private var closing = false
    private var finishTask: FutureTask<RawUsbCaptureStatus>? = null

    init {
        require(queueCapacity > 0 && maxChunkBytes > 0 && maxCaptureBytes >= 8192)
        require(!file.exists() && !partialFile.exists()) { "Raw capture refuses to overwrite an existing file" }
        require(file.parentFile?.isDirectory == true) { "Create the capture directory before starting USB" }
        writer = openWriter(partialFile)
        val metadata = JSONObject(context.toString()).put("synthetic", synthetic)
            .put("source", if (synthetic) "authored_fake_transport" else "operator_pixel_usb_capture")
            .put("tx_capture_point", "native_write_attempt_not_delivery")
        append("session_start", clock(), JSONObject().put("schema_version", 1).put("context", metadata), force = true)
        executor.prestartAllCoreThreads()
    }

    /**
     * [callbackNs] is the driver's observer callback timestamp, not UART arrival
     * or sensor measurement time. SerialInputOutputManager queue latency before
     * this callback is unmeasured. [clock] must use the same host monotonic clock.
     */
    fun record(direction: String, bytes: ByteArray, callbackNs: Long) {
        if (direction !in setOf("rx", "tx") || bytes.isEmpty() || bytes.size > maxChunkBytes || callbackNs < 0) {
            invalidate("invalid or oversized raw USB chunk")
            dropped.incrementAndGet()
            return
        }
        val copy = bytes.copyOf()
        synchronized(lock) {
            if (closing) {
                invalidate("raw USB callback after finalization started")
                dropped.incrementAndGet()
                return
            }
            val handledNs = clock()
            if (handledNs < callbackNs) {
                invalidate("callback/handling clock mismatch")
                dropped.incrementAndGet()
                return
            }
            // One queue slot is permanently reserved for finalization, so a
            // saturated capture can still close and persist its invalidation.
            if (executor.queue.size >= maxPendingRecords) {
                invalidate("raw USB capture queue overflow")
                dropped.incrementAndGet()
                return
            }
            try {
                executor.execute {
                    if (error.get() != null) {
                        dropped.incrementAndGet()
                    } else {
                        val alphabet = "0123456789abcdef"
                        val hex = CharArray(copy.size * 2)
                        copy.forEachIndexed { i, byte ->
                            val value = byte.toInt() and 255
                            hex[i * 2] = alphabet[value ushr 4]
                            hex[i * 2 + 1] = alphabet[value and 15]
                        }
                        append("usb_${direction}_chunk", handledNs, JSONObject()
                            .put("hex", String(hex)).put("received_monotonic_ns", callbackNs))
                    }
                }
            } catch (_: java.util.concurrent.RejectedExecutionException) {
                invalidate("raw USB capture queue overflow")
                dropped.incrementAndGet()
            }
        }
    }

    fun invalidate(reason: String) { error.compareAndSet(null, reason) }

    /**
     * Call only after observing transport teardown. Bounded waiting; a late worker
     * may eventually close an incomplete file, but this call never reports success
     * after timeout. No reconnect is authorized by a capture finishing.
     */
    fun finish(outcome: String, teardown: JSONObject, timeoutMs: Long = 3000): RawUsbCaptureStatus {
        require(timeoutMs > 0)
        val started = System.nanoTime()
        val task: FutureTask<RawUsbCaptureStatus>
        var enqueue = false
        synchronized(lock) {
            task = finishTask ?: FutureTask(Callable {
                val complete = error.get() == null && dropped.get() == 0L
                append("session_end", clock(), JSONObject().put("outcome", outcome)
                    .put("complete", complete).put("dropped_records", dropped.get())
                    .put("capture_error", error.get() ?: JSONObject.NULL)
                    .put("transport_teardown", JSONObject(teardown.toString())), force = true)
                try { writer.flush(); writer.close() } catch (failure: Exception) {
                    invalidate("raw USB capture close failed: ${failure.javaClass.simpleName}")
                }
                if (error.get() == null && dropped.get() == 0L && !partialFile.renameTo(file)) {
                    invalidate("raw USB capture finalization rename failed")
                }
                status()
            }).also { finishTask = it; closing = true; enqueue = true }
        }
        if (enqueue) {
            if (!executor.queue.offer(task, timeoutMs, TimeUnit.MILLISECONDS)) {
                invalidate("raw USB capture finalization queue timed out")
                executor.shutdown()
                return status().copy(complete = false)
            }
            executor.shutdown()
        }
        val remaining = timeoutMs * 1_000_000 - (System.nanoTime() - started)
        return try {
            if (remaining <= 0) throw java.util.concurrent.TimeoutException()
            val result = task.get(remaining, TimeUnit.NANOSECONDS)
            if (error.get() == null) result else status().copy(complete = false)
        } catch (failure: Exception) {
            invalidate("raw USB capture finalization failed: ${failure.javaClass.simpleName}")
            status().copy(complete = false)
        }
    }

    private fun append(type: String, ns: Long, data: JSONObject, force: Boolean = false) {
        try {
            val line = JSONObject().put("type", type).put("session_id", sessionId)
                .put("record_index", recordIndex).put("host_monotonic_ns", ns)
                .put("data", data).toString() + "\n"
            val size = line.toByteArray(Charsets.UTF_8).size
            // Reserve room for the final invalidation record if the cap is reached.
            if (writtenBytes + size > maxCaptureBytes - (if (force) 0 else 4096)) {
                invalidate("raw USB capture size cap reached")
                dropped.incrementAndGet()
                return
            }
            writer.write(line)
            writtenBytes += size
            recordIndex++
            if (force || recordIndex % 100 == 0L) writer.flush()
        } catch (failure: Exception) {
            invalidate("raw USB capture write failed: ${failure.javaClass.simpleName}")
            dropped.incrementAndGet()
        }
    }
}
