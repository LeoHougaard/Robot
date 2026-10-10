package com.leo.pixelrobot.policy

import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread
import org.junit.Assert.*
import org.junit.Test

class ShadowRequestCoordinatorTest {
    private class Clock(var time: Long = 1_000) { fun now() = time }
    private val clock = Clock()
    private fun coordinator() = ShadowRequestCoordinator(capabilities, clock::now)
    private fun velocity(duration: Int = 100) = """{"schema_version":1,"kind":"velocity_request","frame":"body","forward_m_s":0.02,"lateral_m_s":-0.01,"yaw_rate_rad_s":0.08,"duration_ms":$duration}"""
    private fun begin(coordinator: ShadowRequestCoordinator, optIn: Boolean = true) = coordinator.beginOperatorRequest("Walk a tiny step", optIn)

    @Test fun idleHasBoundIdentityButNoAuthorityOrPreview() {
        val status = coordinator().status
        assertEquals(ShadowServiceState.IDLE, status.state)
        assertEquals(capabilities.digest, status.capabilityDigest)
        assertEquals(5, status.assetSha256.size)
        assertNull(status.preview)
        assertFalse(status.physicalExecution); assertFalse(status.physicalStopConfirmed); assertFalse(status.policyActorInference)
    }

    @Test fun perRequestExplicitGrantPreservesAxesAndLabelsAdmissionOnly() {
        val c = coordinator(); val handle = begin(c)
        assertEquals(clock.time + 10_000, handle.expiresAtMs)
        assertEquals(ShadowServiceState.PENDING, c.updates.value.state)
        val status = c.submitResponse(handle, velocity())
        assertEquals(ShadowServiceState.SHADOW_ACCEPTED, status.state)
        assertEquals(.02, status.preview!!.forward, 0.0)
        assertEquals(-.01, status.preview!!.lateral, 0.0)
        assertEquals(.08, status.preview!!.yawRate, 0.0)
        val json = status.toJson()
        assertEquals(0, json.getJSONObject("preview").getInt("height_offset_m"))
        assertEquals(0, json.getJSONObject("preview").getInt("roll_rad"))
        assertEquals(0, json.getJSONObject("preview").getInt("pitch_rad"))
        assertFalse(json.getBoolean("physical_execution"))
        assertFalse(json.getBoolean("physical_stop_confirmed"))
        assertFalse(json.getBoolean("policy_actor_inference"))
    }

    @Test fun unqualifiedCandidateNeedsSeparateExplicitOptIn() {
        val c = coordinator(); val result = c.submitResponse(begin(c, false), velocity())
        assertEquals(ShadowServiceState.REJECTED, result.state)
        assertEquals("unqualified_shadow_opt_in_required", result.reason)
    }

    @Test fun activeAndPendingAreLatestExplicitRequestWins() {
        val c = coordinator(); val first = begin(c)
        c.submitResponse(first, velocity())
        val second = begin(c)
        assertEquals(ShadowServiceState.PENDING, c.status.state)
        assertNull(c.status.preview)
        assertEquals(second.requestId, c.submitResponse(first, velocity()).requestId)
        val third = begin(c)
        assertEquals(third.requestId, c.submitResponse(second, velocity()).requestId)
        assertEquals(ShadowServiceState.SHADOW_ACCEPTED, c.submitResponse(third, velocity()).state)
    }

    @Test fun aCopiedOrOtherCoordinatorHandleCannotPublish() {
        val c = coordinator(); val handle = begin(c)
        val copied = ShadowRequestHandle(handle.text, handle.requestId, handle.expiresAtMs, handle.capabilityDigest)
        assertEquals(ShadowServiceState.PENDING, c.submitResponse(copied, velocity()).state)
        val other = coordinator(); val foreign = begin(other)
        assertEquals(ShadowServiceState.PENDING, c.submitResponse(foreign, velocity()).state)
        assertEquals(ShadowServiceState.SHADOW_ACCEPTED, c.submitResponse(handle, velocity()).state)
    }

    @Test fun independentPollExpiresPendingAndAdmittedWithoutAnyProviderCallback() {
        val c = coordinator(); val handle = begin(c)
        clock.time = handle.expiresAtMs
        assertEquals(ShadowServiceState.EXPIRED, c.poll().state)
        assertNull(c.status.preview)
        val next = begin(c)
        c.submitResponse(next, velocity())
        clock.time += 100
        assertEquals(ShadowServiceState.EXPIRED, c.poll().state)
        assertNull(c.updates.value.preview)
    }

    @Test fun exactDeadlineCallbackExpiresEvenWithoutTimerTick() {
        val c = coordinator(); val handle = begin(c)
        clock.time = handle.expiresAtMs
        assertEquals(ShadowServiceState.EXPIRED, c.submitResponse(handle, velocity()).state)
        assertEquals("shadow_lease_expired", c.status.reason)
    }

    @Test fun providerTimeCountsAndNeverRenewsOriginalDeadline() {
        val c = coordinator(); val handle = begin(c)
        assertEquals(clock.time + 8_000, handle.providerResultExpiresAtMs)
        val originalDeadline = handle.expiresAtMs
        clock.time = handle.providerResultExpiresAtMs - 1
        assertEquals(ShadowServiceState.SHADOW_ACCEPTED, c.submitResponse(handle, velocity(1_000)).state)
        assertTrue(c.status.expiresAtMs!! < originalDeadline)
        assertEquals(originalDeadline, handle.expiresAtMs)
    }

    @Test fun exactAndLateProviderCutoffCannotClaimOrPublishEvenBeforeHostEnvelopeExpires() {
        for (lateBy in listOf(0L, 1L, 999L)) {
            val c = coordinator(); val handle = begin(c)
            clock.time = handle.providerResultExpiresAtMs + lateBy
            var claimed = false
            val status = c.submitResponse(handle, velocity()) { claimed = true; true }
            assertFalse(claimed)
            assertEquals(ShadowServiceState.EXPIRED, status.state)
            assertEquals("provider_result_deadline_elapsed", status.reason)
            assertNull(status.preview)
        }
    }

    @Test fun providerDeadlineIncludesClaimAndAdmissionTime() {
        val c = coordinator(); val handle = begin(c)
        val status = c.submitResponse(handle, velocity()) {
            clock.time = handle.providerResultExpiresAtMs
            true
        }
        assertEquals(ShadowServiceState.EXPIRED, status.state)
        assertEquals("provider_result_deadline_elapsed", status.reason)
        assertNull(status.preview)
    }

    @Test fun leaseExpiringDuringFinalAdmissionCheckNeverPublishesStaleAcceptedPreview() {
        var responseClock = false
        var responseReads = 0
        val c = ShadowRequestCoordinator(capabilities) {
            if (responseClock && ++responseReads >= 4) 1_200L else 1_000L
        }
        val handle = begin(c)
        responseClock = true
        val result = c.submitResponse(handle, velocity(100))
        assertEquals(ShadowServiceState.EXPIRED, result.state)
        assertEquals("shadow_lease_expired", result.reason)
        assertNull(result.preview)
    }

    @Test fun timeoutWinningPublicationBarrierCannotAdmitWhenFailureCallbackIsLate() {
        val c = coordinator(); val handle = begin(c)
        val arrived = CountDownLatch(1); val release = CountDownLatch(1)
        val publicationAvailable = AtomicBoolean(true)
        val provider = thread {
            c.submitResponse(handle, velocity()) {
                arrived.countDown(); check(release.await(2, TimeUnit.SECONDS))
                publicationAvailable.compareAndSet(true, false)
            }
        }
        assertTrue(arrived.await(2, TimeUnit.SECONDS))
        // Timeout wins the provider fence while the failure callback waits for host lock.
        assertTrue(publicationAvailable.compareAndSet(true, false))
        val failure = thread { c.failRequest(handle) }
        release.countDown(); provider.join(2_000); failure.join(2_000)
        assertFalse(provider.isAlive); assertFalse(failure.isAlive)
        assertEquals(ShadowServiceState.CANCELLED, c.status.state)
        assertEquals("provider_publication_cancelled", c.status.reason)
        assertNull(c.status.preview)
    }

    @Test fun publicationWinningBarrierLeavesTimeoutWithoutOwnership() {
        val c = coordinator(); val handle = begin(c)
        val claimed = CountDownLatch(1); val release = CountDownLatch(1)
        val publicationAvailable = AtomicBoolean(true)
        val provider = thread {
            c.submitResponse(handle, velocity()) {
                val winner = publicationAvailable.compareAndSet(true, false)
                claimed.countDown(); check(release.await(2, TimeUnit.SECONDS)); winner
            }
        }
        assertTrue(claimed.await(2, TimeUnit.SECONDS))
        assertFalse(publicationAvailable.compareAndSet(true, false))
        release.countDown(); provider.join(2_000)
        assertFalse(provider.isAlive)
        assertEquals(ShadowServiceState.SHADOW_ACCEPTED, c.status.state)
        // A duplicate/late error still cannot override a correctly published result.
        assertEquals(ShadowServiceState.SHADOW_ACCEPTED, c.failRequest(handle).state)
    }

    @Test fun everyLifecycleAndManualReasonRevokesPendingAndActiveResults() {
        for (reason in ShadowCancelReason.entries) {
            for (admitFirst in listOf(false, true)) {
                val c = coordinator(); val handle = begin(c)
                if (admitFirst) c.submitResponse(handle, velocity())
                c.cancel(reason)
                assertEquals(reason.name.lowercase(), c.status.reason)
                assertNull(c.status.preview); assertNull(c.currentHandle)
                assertEquals(ShadowServiceState.CANCELLED, c.submitResponse(handle, velocity()).state)
                c.close()
            }
        }
    }

    @Test fun stoppedPreviewNeedsFreshExplicitRequestAndNeverClaimsPhysicalStop() {
        val c = coordinator(); val handle = begin(c)
        val stopped = c.stop()
        assertEquals(ShadowServiceState.STOP_REQUESTED, stopped.state)
        assertFalse(stopped.physicalStopConfirmed)
        assertEquals(stopped, c.submitResponse(handle, velocity()))
        val next = begin(c)
        assertNotEquals(handle.requestId, next.requestId)
        assertEquals(ShadowServiceState.SHADOW_ACCEPTED, c.submitResponse(next, velocity()).state)
    }

    @Test fun modelStopOnlyStopsShadowAndLateVelocityCannotRestartIt() {
        val c = coordinator(); val handle = begin(c)
        val status = c.submitResponse(handle, """{"schema_version":1,"kind":"stop_request"}""")
        assertEquals(ShadowServiceState.STOP_REQUESTED, status.state)
        assertFalse(status.physicalStopConfirmed)
        assertEquals(status, c.submitResponse(handle, velocity()))
        assertEquals(ShadowServiceState.PENDING, c.run { begin(this); this.status }.state)
    }

    @Test fun closeIsPermanentAndOldHandleCannotCrossServiceRecreation() {
        val c = coordinator(); val handle = begin(c); c.close(); c.close()
        assertEquals(ShadowServiceState.CLOSED, c.submitResponse(handle, velocity()).state)
        assertTrue(runCatching { begin(c) }.isFailure)
        val recreated = coordinator(); val newHandle = begin(recreated)
        assertEquals(newHandle.requestId, recreated.submitResponse(handle, velocity()).requestId)
        assertEquals(ShadowServiceState.PENDING, recreated.status.state)
    }

    @Test fun clockRollbackDuringSubmitOrPollFailsClosed() {
        for (duringSubmit in listOf(false, true)) {
            val c = coordinator(); val handle = begin(c)
            clock.time -= 1
            val status = if (duringSubmit) c.submitResponse(handle, velocity()) else c.poll()
            assertEquals(ShadowServiceState.UNAVAILABLE, status.state)
            assertNull(status.preview)
            assertNull(c.currentHandle)
            c.close(); clock.time = 1_000
        }
    }

    @Test fun clockFailureDuringBeginCannotLeaveEarlierPreviewPublished() {
        val c = coordinator(); c.submitResponse(begin(c), velocity())
        clock.time -= 1
        assertTrue(runCatching { begin(c) }.isFailure)
        assertEquals(ShadowServiceState.UNAVAILABLE, c.status.state)
        assertNull(c.status.preview)
    }

    @Test fun lateFailureDoesNotCancelNewerWorkAndRepeatedReplyDoesNotChangeIt() {
        val c = coordinator(); val old = begin(c); val next = begin(c)
        assertEquals(next.requestId, c.failRequest(old).requestId)
        val accepted = c.submitResponse(next, velocity())
        assertEquals(accepted, c.failRequest(next))
        assertEquals(accepted, c.submitResponse(next, "bad JSON"))
    }

    @Test fun cancelledSlowProviderCannotPublishAfterItReturns() {
        val c = coordinator(); val handle = begin(c)
        val ready = CountDownLatch(1); val release = CountDownLatch(1)
        val provider = thread {
            ready.countDown(); assertTrue(release.await(2, TimeUnit.SECONDS))
            c.submitResponse(handle, velocity())
        }
        assertTrue(ready.await(2, TimeUnit.SECONDS))
        c.cancel(ShadowCancelReason.MANUAL_CONTROL); release.countDown(); provider.join(2_000)
        assertFalse(provider.isAlive)
        assertEquals(ShadowServiceState.CANCELLED, c.updates.value.state)
        assertNull(c.status.preview)
    }

    @Test fun malformedIntentClarificationAndUnsupportedHaveTypedOutcomes() {
        val c = coordinator()
        assertEquals(ShadowServiceState.REJECTED, c.submitResponse(begin(c), velocity().dropLast(1)).state)
        val clarification = c.submitResponse(begin(c), """{"schema_version":1,"kind":"clarify","question":"Which direction?"}""")
        assertEquals("Which direction?", clarification.question); assertNull(clarification.preview)
        val unsupported = c.submitResponse(begin(c), """{"schema_version":1,"kind":"unsupported","reason":"No world navigation"}""")
        assertEquals("No world navigation", unsupported.unsupportedReason); assertNull(unsupported.preview)
    }

    @Test fun invalidTextDoesNotReplaceCurrentRequestAndUnicodeBoundIsCodepoints() {
        val c = coordinator(); val original = begin(c)
        for (invalid in listOf("", "   ", "x".repeat(2_001), "\ud800")) {
            assertTrue(runCatching { c.beginOperatorRequest(invalid, true) }.isFailure)
            assertEquals(original.requestId, c.status.requestId)
        }
        val emoji = "\ud83d\udc15".repeat(2_000)
        assertEquals(emoji, c.beginOperatorRequest(emoji, true).text)
    }

    companion object {
        private val assets = File("src/main/assets")
        private val capabilities = ShadowCapabilities.fromBundle(
            File(assets, "policy_metadata.json").readBytes(), File(assets, "policy_android_manifest.json").readBytes(),
            File(assets, "policy_actor.onnx").readBytes(), File(assets, "stride_reference.json").readBytes(),
            File(assets, "assembly-four-leg-linkage-12dof.calibration.json").readBytes())
    }
}
