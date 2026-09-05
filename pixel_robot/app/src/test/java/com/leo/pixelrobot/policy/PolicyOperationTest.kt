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
