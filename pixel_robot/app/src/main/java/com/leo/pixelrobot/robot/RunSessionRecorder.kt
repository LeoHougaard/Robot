package com.leo.pixelrobot.robot

import org.json.JSONObject
import java.io.BufferedWriter
import java.io.Closeable
import java.io.File
import java.io.FileOutputStream
import java.io.OutputStreamWriter
import java.nio.charset.StandardCharsets
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.UUID
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

data class RunRecordingStatus(
    val active: Boolean,
    val activeFileName: String?,
    val latestFileName: String?,
    val recordCount: Long,
    val bytesWritten: Long,
    val error: String?,
)

/** Bounded asynchronous evidence recording; storage cannot block the control producer. */
class RunSessionRecorder(
    private val directory: File,
    private val contextProvider: () -> JSONObject,
    private val unixTimeMillis: () -> Long = System::currentTimeMillis,
    private val monotonicNanos: () -> Long = System::nanoTime,
    private val openWriter: (File) -> BufferedWriter = { output ->
        BufferedWriter(OutputStreamWriter(FileOutputStream(output), StandardCharsets.UTF_8), WRITER_BUFFER_BYTES)
    },
) : Closeable {
    private val recordExecutor = ThreadPoolExecutor(
        1, 1, 0L, TimeUnit.MILLISECONDS, ArrayBlockingQueue(256),
        { runnable -> Thread(runnable, "robot-run-recorder").apply { isDaemon = true } },
        ThreadPoolExecutor.AbortPolicy(),
    )
    private val droppedRecords = AtomicLong()
    @Volatile private var writer: BufferedWriter? = null
    private var partialFile: File? = null
    private var completedFile: File? = null
    private var sessionId: String? = null
    private var recordCount = 0L
    private var bytesWritten = 0L
    @Volatile private var lastError: String? = null

    fun start(phase: String): RunRecordingStatus {
        require(phase.isNotBlank())
        drainQueuedRecords()
        return synchronized(this) {
            if (writer != null) {
                recordLocked("phase_start", JSONObject().put("phase", phase), flush = true)
                return@synchronized statusLocked()
            }

            try {
                directory.mkdirs()
                require(directory.isDirectory) { "run session directory is unavailable: $directory" }
                recoverInterruptedSessionsLocked()
                val context = JSONObject(contextProvider().toString())
                val startedAt = unixTimeMillis()
                val id = UUID.randomUUID().toString()
                val stamp = UTC_FILE_STAMP.format(Date(startedAt))
                val output = File(directory, "robot-run-$stamp-${id.take(8)}.jsonl.partial")
                writer = openWriter(output)
                partialFile = output
                completedFile = null
                sessionId = id
                recordCount = 0L
                bytesWritten = 0L
                lastError = null
                droppedRecords.set(0)
                recordLocked(
                    "session_start",
                    JSONObject()
                        .put("schema_version", SCHEMA_VERSION)
                        .put("phase", phase)
                        .put("context", context),
                    flush = true,
                    unixMs = startedAt,
                )
                statusLocked()
            } catch (error: Throwable) {
                lastError = error.message ?: error.javaClass.simpleName
                runCatching { writer?.close() }
                writer = null
                partialFile?.takeIf { it.length() == 0L }?.delete()
                partialFile = null
                sessionId = null
                statusLocked()
            }
        }
    }

    fun recordEvent(name: String, data: JSONObject = JSONObject()) {
        val copy = JSONObject(data.toString())
        enqueueRecord { unixMs, monotonicNs ->
            recordLocked(
                "event",
                JSONObject().put("name", name).put("values", copy),
                flush = true, unixMs = unixMs, monotonicNs = monotonicNs,
            )
        }
    }

    fun recordRobotRx(message: JSONObject) {
        val copy = JSONObject(message.toString())
        enqueueRecord { unixMs, monotonicNs ->
            recordLocked("robot_rx", JSONObject().put("message", copy), unixMs = unixMs, monotonicNs = monotonicNs)
        }
    }

    fun recordRobotTx(bytes: ByteArray) {
        val copy = bytes.copyOf()
        enqueueRecord { unixMs, monotonicNs ->
            val text = copy.toString(Charsets.UTF_8).trim()
            val values = JSONObject()
            runCatching { JSONObject(text) }
                .onSuccess { values.put("message", it) }
                .onFailure { values.put("text", text) }
            recordLocked("robot_tx", values, unixMs = unixMs, monotonicNs = monotonicNs)
        }
    }

    fun recordDerivedFrame(values: JSONObject) {
        val copy = JSONObject(values.toString())
        enqueueRecord { unixMs, monotonicNs ->
            recordLocked("derived_policy_frame", copy, unixMs = unixMs, monotonicNs = monotonicNs)
        }
    }

    fun finish(outcome: String, detail: String): RunRecordingStatus {
        drainQueuedRecords()
        return synchronized(this) {
            if (writer == null) return@synchronized statusLocked()
            runCatching {
                recordLocked(
                    "session_end",
                    JSONObject().put("outcome", outcome).put("detail", detail)
                        .put("dropped_records", droppedRecords.get()).put("complete", lastError == null),
                    flush = true,
                )
                if (writer == null) return@synchronized statusLocked()
                writer?.close()
                writer = null
                val source = requireNotNull(partialFile)
                val destination = File(source.parentFile, source.name.removeSuffix(".partial"))
                require(source.renameTo(destination)) { "could not finalize run session $source" }
                completedFile = destination
                partialFile = null
            }.onFailure { error ->
                lastError = error.message ?: error.javaClass.simpleName
                runCatching { writer?.close() }
                writer = null
            }
            statusLocked()
        }
    }

    @Synchronized
    fun status(): RunRecordingStatus = statusLocked()

    @Synchronized
    fun latestCompletedFile(): File? {
        val known = completedFile?.takeIf(File::isFile)
        if (known != null) return known
        return directory.listFiles { file -> file.isFile && file.name.endsWith(".jsonl") }
            ?.maxByOrNull(File::lastModified)
            ?.also { completedFile = it }
    }

    override fun close() {
        if (writer != null) finish("aborted", "recorder closed")
        recordExecutor.shutdown()
    }

    private fun enqueueRecord(action: (Long, Long) -> Unit) {
        val sessionWriter = writer ?: return
        if (recordExecutor.isShutdown) return
        // Capture at the producer, before storage delay; never acquire the
        // writer's monitor on the USB or policy thread.
        val unixMs = unixTimeMillis()
        val monotonicNs = monotonicNanos()
        try {
            recordExecutor.execute {
                synchronized(this) { if (writer === sessionWriter) action(unixMs, monotonicNs) }
            }
        } catch (_: RejectedExecutionException) {
            droppedRecords.incrementAndGet()
            lastError = "Recording incomplete: storage queue overflowed"
        }
    }

    private fun drainQueuedRecords() {
        if (recordExecutor.isShutdown) return
        // Lifecycle calls may wait; control producers above never do.
        val barrier = java.util.concurrent.FutureTask<Unit> { Unit }
        check(recordExecutor.queue.offer(barrier, 5, TimeUnit.SECONDS)) { "recorder did not drain" }
        recordExecutor.prestartCoreThread()
        barrier.get(5, TimeUnit.SECONDS)
    }

    private fun recordLocked(
        type: String,
        data: JSONObject,
        flush: Boolean = false,
        unixMs: Long = unixTimeMillis(),
        monotonicNs: Long = monotonicNanos(),
    ) {
        try {
            val output = requireNotNull(writer) { "run session is not active" }
            val record = JSONObject()
                .put("type", type)
                .put("session_id", sessionId)
                .put("record_index", recordCount)
                .put("host_unix_ms", unixMs)
                .put("host_monotonic_ns", monotonicNs)
                .put("data", data)
            val line = record.toString() + "\n"
            val encodedBytes = line.toByteArray(StandardCharsets.UTF_8).size
            require(bytesWritten + encodedBytes <= MAX_SESSION_BYTES) {
                "run session exceeded ${MAX_SESSION_BYTES / (1024 * 1024)} MiB"
            }
            output.write(line)
            recordCount += 1
            bytesWritten += encodedBytes
            if (flush || recordCount % FLUSH_EVERY_RECORDS == 0L) output.flush()
        } catch (error: Throwable) {
            lastError = error.message ?: error.javaClass.simpleName
            runCatching { writer?.close() }
            writer = null
        }
    }

    private fun statusLocked(): RunRecordingStatus = RunRecordingStatus(
        active = writer != null,
        activeFileName = partialFile?.name,
        latestFileName = latestCompletedFile()?.name,
        recordCount = recordCount,
        bytesWritten = bytesWritten,
        error = lastError,
    )

    private fun recoverInterruptedSessionsLocked() {
        directory.listFiles { file -> file.isFile && file.name.endsWith(".jsonl.partial") }
            ?.forEach { source ->
                val destination = File(
                    source.parentFile,
                    source.name.removeSuffix(".jsonl.partial") + ".interrupted.jsonl",
                )
                if (!destination.exists() && source.renameTo(destination)) {
                    completedFile = destination
                }
            }
    }

    companion object {
        const val SCHEMA_VERSION = 1
        private const val WRITER_BUFFER_BYTES = 64 * 1024
        private const val FLUSH_EVERY_RECORDS = 25L
        private const val MAX_SESSION_BYTES = 512L * 1024L * 1024L
        private val UTC_FILE_STAMP = SimpleDateFormat("yyyyMMdd-HHmmss-SSS", Locale.US).apply {
            timeZone = TimeZone.getTimeZone("UTC")
        }
    }
}
