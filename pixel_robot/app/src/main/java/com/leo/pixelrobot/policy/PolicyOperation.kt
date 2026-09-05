package com.leo.pixelrobot.policy

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch

/** Orders cancellation and transport writes; a stopping job still owns the operation. */
class PolicyOperation {
    private var job: Job? = null
    private var closed = false

    @Synchronized
    fun start(scope: CoroutineScope, block: suspend () -> Unit) {
        check(!closed) { "policy runtime is closed" }
        check(job?.isCompleted != false) { "another robot operation is still running or stopping" }
        job = scope.launch(start = CoroutineStart.LAZY) { block() }.also { it.start() }
    }

    suspend fun send(write: () -> Unit) {
        val context = currentCoroutineContext()
        synchronized(this) {
            context.ensureActive()
            check(!closed && job?.isActive == true) { "robot operation is no longer active" }
            write()
        }
    }

    @Synchronized
    fun stop(reason: String, disarm: () -> Unit) {
        job?.cancel(CancellationException(reason))
        disarm()
    }

    @Synchronized
    fun close(disarm: () -> Unit, release: () -> Unit) {
        if (closed) return
        closed = true
        stop("Runtime closed", disarm)
        val current = job
        if (current == null) release() else current.invokeOnCompletion { release() }
    }
}
