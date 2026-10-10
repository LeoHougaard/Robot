package com.leo.pixelrobot.policy

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch
import kotlin.coroutines.AbstractCoroutineContextElement
import kotlin.coroutines.CoroutineContext

/** Orders cancellation and transport writes; a stopping job still owns the operation. */
class PolicyOperation {
    private var job: Job? = null
    private var closed = false
    private var owner: Owner? = null
    private class Owner : AbstractCoroutineContextElement(Key) {
        companion object Key : CoroutineContext.Key<Owner>
    }

    @Synchronized
    fun start(scope: CoroutineScope, prepare: () -> Unit = {}, block: suspend () -> Unit) {
        check(!closed) { "policy runtime is closed" }
        check(job?.isCompleted != false) { "another robot operation is still running or stopping" }
        scope.coroutineContext.ensureActive()
        // Commit a requested command only after admission succeeds. A rejected
        // duplicate start must not change an operation already controlling.
        prepare()
        val nextOwner = Owner()
        val nextJob = scope.launch(nextOwner, start = CoroutineStart.LAZY) { block() }
        owner = nextOwner
        job = nextJob
        // Unconfined/immediate dispatchers can enter block synchronously.
        // Publish the job before starting so its first write sees its owner.
        nextJob.start()
    }

    suspend fun send(write: () -> Unit) {
        val context = currentCoroutineContext()
        synchronized(this) {
            context.ensureActive()
            check(!closed && job?.isActive == true) { "robot operation is no longer active" }
            check(context[Owner] === owner) { "write does not belong to the current robot operation" }
            write()
        }
    }

    /** Dispatch may queue work. The captured owner is checked at the actual driver write. */
    suspend fun dispatch(dispatch: ((() -> Unit) -> Unit) -> Unit) {
        val context = currentCoroutineContext()
        dispatch { write ->
            synchronized(this) {
                context.ensureActive()
                check(!closed && job?.isActive == true && context[Owner] === owner) {
                    "queued write does not belong to an active robot operation"
                }
                write()
            }
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
        try {
            stop("Runtime closed", disarm)
        } finally {
            val current = job
            if (current == null) release() else current.invokeOnCompletion { release() }
        }
    }
}
