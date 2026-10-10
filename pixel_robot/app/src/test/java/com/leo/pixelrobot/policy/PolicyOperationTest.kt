package com.leo.pixelrobot.policy

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancel
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.junit.Assert.*
import org.junit.Test

class PolicyOperationTest {
    @Test(timeout = 5000) fun queuedWriteRechecksOwnerAfterStopAndReplacement() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val operation = PolicyOperation()
        var queued: ((() -> Unit) -> Unit)? = null
        try {
            operation.start(scope) { operation.dispatch { queued = it }; awaitCancellation() }
            operation.stop("stop") {}
            operation.start(scope) { awaitCancellation() }
            assertTrue(runCatching { queued!! { fail("old queued command reached driver") } }.isFailure)
        } finally { operation.close({}, {}); scope.cancel() }
    }
    @Test(timeout = 5000)
    fun duplicateStartCannotMutateTheRunningCommand() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val operation = PolicyOperation()
        var command = "idle"
        try {
            operation.start(scope, prepare = { command = "first" }) { awaitCancellation() }
            assertTrue(runCatching {
                operation.start(scope, prepare = { command = "rejected" }) { fail("duplicate ran") }
            }.isFailure)
            assertEquals("first", command)
            operation.stop("stop") { command = "zero" }
            assertEquals("zero", command)
        } finally {
            operation.close({}, {})
            scope.cancel()
        }
    }

    @Test(timeout = 5000)
    fun unrelatedCoroutineCannotWriteIntoActiveOperation() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val operation = PolicyOperation()
        val writes = mutableListOf<String>()
        try {
            operation.start(scope) {
                operation.send { writes += "owner" }
                awaitCancellation()
            }
            assertTrue(runCatching { operation.send { writes += "foreign" } }.isFailure)
            operation.stop("stop") { writes += "stop" }
            assertEquals(listOf("owner", "stop"), writes)
        } finally {
            operation.close({}, {})
            scope.cancel()
        }
    }

    @Test
    fun cancelledScopeCannotStartAnotherOperation() {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        scope.cancel()
        val operation = PolicyOperation()
        assertTrue(runCatching { operation.start(scope) { fail("cancelled runtime ran a command") } }.isFailure)
    }

    @Test
    fun failingDisarmStillReleasesExactlyOnceAndClosesRuntime() {
        val operation = PolicyOperation()
        var releases = 0
        assertTrue(runCatching { operation.close({ error("transport lost") }, { releases += 1 }) }.isFailure)
        operation.close({ fail("closed runtime disarmed twice") }, { releases += 1 })
        assertEquals(1, releases)
    }

    @Test(timeout = 5000)
    fun immediateDispatcherPublishesOperationBeforeFirstWrite() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Unconfined)
        val operation = PolicyOperation()
        val writes = mutableListOf<String>()
        var failure: Throwable? = null
        try {
            operation.start(scope) {
                failure = runCatching { operation.send { writes += "command" } }.exceptionOrNull()
            }
            assertNull(failure)
            assertEquals(listOf("command"), writes)
            operation.start(scope) { operation.send { writes += "restart" } }
            assertEquals(listOf("command", "restart"), writes)
        } finally {
            operation.close({}, {})
            scope.cancel()
        }
    }

    @Test(timeout = 5000)
    fun stopRejectsLateWritesAndRestartUntilCleanupCompletes() = runBlocking {
        val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
        val operation = PolicyOperation()
        val started = CompletableDeferred<Unit>()
        val cleaning = CompletableDeferred<Unit>()
        val releaseCleanup = CompletableDeferred<Unit>()
        val released = CompletableDeferred<Unit>()
        val writes = java.util.Collections.synchronizedList(mutableListOf<String>())
        try {
            operation.start(scope) {
                try {
                    operation.send { writes += "command" }
                    started.complete(Unit)
                    awaitCancellation()
                } finally {
                    withContext(NonCancellable) {
                        cleaning.complete(Unit)
                        releaseCleanup.await()
                        assertTrue(runCatching { operation.send { writes += "late command" } }.isFailure)
                    }
                }
            }
            withTimeout(1000) { started.await() }
            operation.stop("operator stop") { writes += "stop" }
            withTimeout(1000) { cleaning.await() }
            assertTrue(runCatching { operation.start(scope) {} }.isFailure)
            operation.close({}, { released.complete(Unit) })
            assertFalse(released.isCompleted)
            releaseCleanup.complete(Unit)
            withTimeout(1000) { released.await() }
            assertEquals(listOf("command", "stop"), writes)
            assertTrue(runCatching { operation.start(scope) {} }.isFailure)
        } finally {
            releaseCleanup.complete(Unit)
            scope.cancel()
        }
    }
}
