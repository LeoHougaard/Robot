package com.leo.pixelrobot.shadow

import java.io.Closeable
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.ThreadFactory
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import org.json.JSONObject

/** Bounded request worker, independent timeout and publication fence. H is a local
 * opaque host handle, never model data or an HTTP parameter. Host callbacks run
 * outside our lock, avoiding service/cancellation lock inversion. This class has
 * no motor/USB/controller methods, nor any ability to turn a preview into motion.
 */
class ShadowProviderRunner<H>(
    private val beginRequest: (String, Boolean) -> H,
    private val submitResponse: (H, String, () -> Boolean) -> Unit,
    private val failRequest: (H) -> Unit,
    private val cancelRequest: () -> Unit,
    private val stopPreview: () -> Unit,
    private val serviceStatus: () -> JSONObject,
    private val callFactory: (ShadowEndpointConfig, String) -> ShadowProviderCall = { config, text -> HttpShadowProviderCall(config, text) },
    private val timeoutMs: Long = ShadowProviderProtocol.TIMEOUT_MS.toLong(),
    private val clockNanos: () -> Long = System::nanoTime,
) : Closeable {
    private val lock = Any()
    private val threads = ThreadFactory { task -> Thread(task, "pixel-shadow-provider").apply { isDaemon = true } }
    private val worker = ThreadPoolExecutor(1, 1, 0, TimeUnit.MILLISECONDS, ArrayBlockingQueue(1), threads)
    private val timer = Executors.newSingleThreadScheduledExecutor(threads)
    private var generation = 0L
    private var closed = false
    private var current: Work? = null
    private var state = "idle"
    private var error: String? = null
    private inner class Work(val id: Long, val call: ShadowProviderCall) {
        val deadlineNanos = Math.addExact(clockNanos(), timeoutMs * 1_000_000L)
        var handle: H? = null
        val revokedWhileBeginning = java.util.IdentityHashMap<H, Boolean>()
        var future: Future<*>? = null
        var expiry: ScheduledFuture<*>? = null
    }
    init { require(timeoutMs in 1..ShadowProviderProtocol.TIMEOUT_MS.toLong()) }

    @Synchronized fun submit(config: ShadowEndpointConfig, text: String, allowUnqualifiedMetadata: Boolean): JSONObject {
        ShadowProviderProtocol.requireText(text)
        val call = callFactory(config, text)
        val work = synchronized(lock) {
            check(!closed) { "shadow_provider_closed" }
            invalidateLocked("cancelled")
            Work(++generation, call).also { current = it; state = "requesting"; error = null }
        }
        val handle = try { beginRequest(text, allowUnqualifiedMetadata) } catch (_: Exception) {
            finish(work, "error", ShadowProviderError.HOST_REJECTED, false); return status()
        }
        val admitted = synchronized(lock) {
            if (current !== work || work.revokedWhileBeginning.containsKey(handle)) {
                if (current === work) invalidateLocked("cancelled")
                false
            } else {
                work.revokedWhileBeginning.clear()
                work.handle = handle
                work.expiry = timer.schedule({ finish(work, "timed_out", ShadowProviderError.TIMED_OUT, true) }, timeoutMs, TimeUnit.MILLISECONDS)
                true
            }
        }
        if (!admitted) { runCatching { failRequest(handle) }; return status() }
        try {
            val future = worker.submit providerWork@ {
                try {
                    val ready = synchronized(lock) { current === work && !closed && clockNanos() < work.deadlineNanos }
                    if (!ready) {
                        finish(work, "timed_out", ShadowProviderError.TIMED_OUT, true)
                        return@providerWork
                    }
                    val raw = work.call.execute()
                    // The provider adapter validates raw intent too; fake/injected
                    // providers still go through the service's strict decoder.
                    if (synchronized(lock) { current === work }) {
                        submitResponse(handle, raw) { claimPublication(work) }
                        finish(work, "completed", null, false)
                    }
                } catch (failure: Exception) {
                    val code = (failure as? ShadowProviderException)?.code ?: ShadowProviderError.TRANSPORT_FAILED
                    finish(work, if (code == ShadowProviderError.TIMED_OUT) "timed_out" else "error", code, true)
                }
            }
            synchronized(lock) { if (current === work) work.future = future else future.cancel(true) }
        } catch (_: RejectedExecutionException) {
            finish(work, "error", ShadowProviderError.BUSY, true)
        }
        return status()
    }

    /** Called under the host coordinator lock, after it verifies its handle.
     * Exactly one of publication and timeout can win this transition. The host
     * never acquires this lock while we hold it and call back into the host.
     */
    private fun claimPublication(work: Work): Boolean = synchronized(lock) {
        if (current !== work || closed) false else if (clockNanos() >= work.deadlineNanos) {
            work.expiry?.cancel(false); work.call.cancel()
            current = null; state = "timed_out"; error = ShadowProviderError.TIMED_OUT.name.lowercase()
            false // Host must revoke this handle when publication cannot be claimed.
        } else {
            work.expiry?.cancel(false)
            current = null; state = "completed"; error = null
            true
        }
    }

    private fun finish(work: Work, next: String, code: ShadowProviderError?, fail: Boolean) {
        val active = synchronized(lock) {
            if (current !== work) false else {
                work.call.cancel(); work.expiry?.cancel(false)
                if (next != "completed") work.future?.cancel(true)
                current = null; state = next; error = code?.name?.lowercase(); true
            }
        }
        if (active && fail) work.handle?.let { runCatching { failRequest(it) } }
    }

    private fun invalidateLocked(next: String) {
        current?.let { it.call.cancel(); it.future?.cancel(true); it.expiry?.cancel(false) }
        current = null; generation++; state = next; error = null
        worker.purge() // Cancelled queued calls do not accumulate or run later.
    }

    /** Service lifecycle invalidation: no callback into the service, no I/O/wait. */
    fun cancelProvider(handle: H? = null) {
        synchronized(lock) {
            if (!closed) {
                if (handle == null || current?.handle === handle) invalidateLocked("cancelled")
                else if (current?.handle == null) current?.revokedWhileBeginning?.set(handle, true)
            }
        }
    }
    fun cancel(): JSONObject { cancelProvider(); cancelRequest(); return status() }
    fun stop(): JSONObject {
        synchronized(lock) { if (!closed) invalidateLocked("stopped") }
        stopPreview(); return status()
    }
    fun status(): JSONObject {
        val view = synchronized(lock) {
            JSONObject().put("mode", "SHADOW").put("physical_execution", false).put("physical_stop_confirmed", false)
                .put("provider_state", state).put("provider_error", error ?: JSONObject.NULL)
        }
        return view.put("service", serviceStatus())
    }
    override fun close() {
        synchronized(lock) { if (closed) return; invalidateLocked("closed"); closed = true }
        worker.shutdownNow(); timer.shutdownNow()
    }
}
