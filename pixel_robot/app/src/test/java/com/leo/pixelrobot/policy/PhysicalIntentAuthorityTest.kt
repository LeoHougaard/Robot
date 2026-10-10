package com.leo.pixelrobot.policy

import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Synthetic qualification claims only. No deployed metadata is changed, and no hardware runs. */
class PhysicalIntentAuthorityTest {
    private var now = 1_000_000_000L
    private val operation = Any()
    private fun authority(binding: PhysicalPolicyBinding = qualified) = PhysicalIntentAuthority(binding) { now }
    private fun enable(a: PhysicalIntentAuthority, expected: Long = a.status().generation) =
        a.enable(true, operation, qualified.digest, expected)
    private fun begin(a: PhysicalIntentAuthority) = a.beginRequest("A short body-frame move", a.status().generation)
    private fun raw(duration: Int = 100) = """{"schema_version":1,"kind":"velocity_request","frame":"body","forward_m_s":0.02,"lateral_m_s":-0.01,"yaw_rate_rad_s":0.08,"duration_ms":$duration}"""
    private fun leased(a: PhysicalIntentAuthority, duration: Int = 100): PhysicalRequestHandle {
        enable(a); return begin(a).also { assertEquals("leased", a.submit(it, raw(duration)).state) }
    }
    private fun ticket(a: PhysicalIntentAuthority, sample: Long = 1, observed: Long = now) =
        a.beginFrame(operation, qualified.digest, sample, observed, true)!!
    private fun denied(action: () -> Unit) { assertTrue(runCatching(action).isFailure) }

    @Test fun checkedInBundleRemainsBlockedWithActionableReasons() {
        val a = authority(unqualified)
        assertFalse(a.status().eligible)
        assertEquals(2, a.status().blockers.size)
        denied { a.enable(true, operation, unqualified.digest, 0) }
        assertEquals("blocked", a.status().state)
    }
    @Test fun individualQualificationClaimsAreBothRequired() {
        assertTrue(binding(false, true).blockers.single().startsWith("policy_not_promoted"))
        assertTrue(binding(true, false).blockers.single().startsWith("transport_not_verified"))
        assertTrue(qualified.blockers.isEmpty())
    }
    @Test fun disabledAndRecreatedAuthorityCannotProvideCommands() {
        val a = authority()
        assertNull(a.beginFrame(operation, qualified.digest, 1, now, true))
        denied { begin(a) }
        leased(a)
        assertEquals("disabled", authority().status().state)
    }
    @Test fun optInExactBundleAndFreshGenerationRequired() {
        val a = authority()
        denied { a.enable(false, operation, qualified.digest, 0) }
        denied { a.enable(true, operation, "wrong", 0) }
        denied { enable(a, 1) }
        enable(a)
        denied { enable(a) }
        assertEquals(1L, a.status().generation)
    }
    @Test fun authorityAndPendingExpireIndependentlyOfProviderAndFrames() {
        for (pending in listOf(false, true)) {
            now = 1_000_000_000
            val a = authority(); enable(a); if (pending) begin(a)
            now += PhysicalIntentAuthority.AUTHORITY_NS
            assertEquals("expired", a.status().state)
            assertNotNull(a.takeStopRequest())
            assertNull(a.takeStopRequest())
            denied { begin(a) }
        }
    }
    @Test fun commandExpiryIsExactAndDoesNotRenewAuthority() {
        val a = authority(); leased(a)
        val t = ticket(a)
        now += 100_000_000
        denied { a.withFrameWrite(t) { fail("expired frame claimed") } }
        assertNotNull(a.takeStopRequest())
    }
    @Test fun providerCutoffEnforcedWithoutAnyTimerTick() {
        val a = authority(); enable(a); val handle = begin(a)
        now += PhysicalIntentAuthority.PROVIDER_NS
        assertEquals("cancelled", a.submit(handle, raw()).state)
        assertNotNull(a.takeStopRequest())
    }
    @Test fun publicationClaimFailureCannotAdmit() {
        val a = authority(); enable(a); val h = begin(a)
        assertEquals("cancelled", a.submit(h, raw()) { false }.state)
        assertNull(a.status().command)
    }
    @Test fun originalAuthorityBoundsLateCommandEvenBeforeProviderCutoff() {
        val a = authority(); enable(a); now += 9_900_000_000
        val handle = begin(a)
        now += 1_000_000
        assertEquals("command_outlives_original_authority_deadline", a.submit(handle, raw(100)).reason)
    }
    @Test fun secondRequestAndReplayCannotRenewOneEnable() {
        val a = authority(); val h = leased(a)
        val deadline = a.status().expiresAtMs
        denied { begin(a) }
        now += 1_000_000
        assertEquals(deadline, a.submit(h, raw(1000)).expiresAtMs)
    }
    @Test fun foreignAndCopiedOpaqueHandlesCannotPublish() {
        val a = authority(); enable(a); val h = begin(a)
        val copy = PhysicalRequestHandle(h.requestId, h.expiresAtMs, h.providerDeadlineMs)
        assertEquals("pending", a.submit(copy, raw()).state)
        val other = authority(); enable(other); val foreign = begin(other)
        assertEquals("pending", a.submit(foreign, raw()).state)
        assertEquals("leased", a.submit(h, raw()).state)
    }
    @Test fun oldCompletionCannotRestoreCancelledOrNewAuthority() {
        val a = authority(); enable(a); val old = begin(a)
        a.cancel(a.status().generation); a.takeStopRequest(); a.resetForExplicitManualOperation()
        enable(a); val current = begin(a)
        assertEquals(current.requestId, a.submit(old, raw()).requestId)
        assertEquals("pending", a.status().state)
        assertEquals("leased", a.submit(current, raw()).state)
    }
    @Test fun lateProviderFailureCannotRevokeNewRequest() {
        val a = authority(); enable(a); val old = begin(a)
        a.cancel(a.status().generation); a.takeStopRequest(); a.resetForExplicitManualOperation()
        enable(a); begin(a); a.fail(old)
        assertEquals("pending", a.status().state)
    }
    @Test fun expectedGenerationFencesDelayedEnableAndRequest() {
        val a = authority(); val initial = a.status().generation
        a.cancel(initial)
        denied { enable(a, initial) }
        enable(a); val old = a.status().generation
        a.cancel(old); a.takeStopRequest(); a.resetForExplicitManualOperation(); enable(a)
        denied { a.beginRequest("old request", old) }
    }
    @Test fun cancellationWinsBeforeOrAfterPendingEnable() {
        for (enableFirst in listOf(false, true)) {
            val a = authority(); val initial = a.status().generation
            if (enableFirst) enable(a, initial)
            a.cancel(initial + 1)
            assertEquals(initial + 2, a.status().generation)
            assertEquals("cancelled", a.status().state)
            denied { enable(a, initial) }
        }
    }
    @Test fun staleCancelCannotRevokeNewAuthority() {
        val a = authority(); enable(a)
        denied { a.cancel(0) }
        assertEquals("enabled", a.status().state)
    }
    @Test fun namedAxesAndNeutralPostureArePreserved() {
        val a = authority(); leased(a); val t = ticket(a)
        assertEquals(.02f, t.motion.forward, 0f)
        assertEquals(-.01f, t.motion.lateral, 0f)
        assertEquals(.08f, t.motion.yawRate, 0f)
        assertEquals(0f, t.motion.heightOffset, 0f); assertEquals(0f, t.motion.roll, 0f); assertEquals(0f, t.motion.pitch, 0f)
        var claimed = false
        a.withFrameWrite(t) { claimed = true }
        assertTrue(claimed)
    }
    @Test fun freshAcceptedFeedbackAndOneUseTicketAreRequired() {
        val a = authority(); leased(a); val t = ticket(a)
        now += PhysicalIntentAuthority.FEEDBACK_MAX_AGE_NS
        a.withFrameWrite(t) {}
        denied { a.withFrameWrite(t) {} }
    }
    @Test fun staleFutureDuplicateAndReorderedSamplesFailClosed() {
        for (observed in listOf(now + 1, now - PhysicalIntentAuthority.FEEDBACK_MAX_AGE_NS - 1)) {
            val a = authority(); leased(a); denied { ticket(a, observed = observed) }
            assertNotNull(a.takeStopRequest())
        }
        val a = authority(); leased(a); a.withFrameWrite(ticket(a)) {}
        now += 1_000_000
        denied { ticket(a, sample = 1) }
    }
    @Test fun staleAtDispatchRejectsFrameComputedWhileFresh() {
        val a = authority(); leased(a); val t = ticket(a)
        now += PhysicalIntentAuthority.FEEDBACK_MAX_AGE_NS + 1
        denied { a.withFrameWrite(t) { fail("stale frame") } }
        assertNotNull(a.takeStopRequest())
    }
    @Test fun wrongOperationOrBundleCannotReadLeasedCommand() {
        for (wrongOperation in listOf(false, true)) {
            val a = authority(); leased(a)
            denied { a.beginFrame(if (wrongOperation) Any() else operation,
                if (wrongOperation) qualified.digest else "wrong", 1, now, true) }
            assertNotNull(a.takeStopRequest())
        }
    }
    @Test fun forgedFrameAndSecondOutstandingFrameRejected() {
        val a = authority(); leased(a); val real = ticket(a)
        denied { ticket(a, 2) }
        val copy = PhysicalFrameTicket(real.lease, real.generation, real.operation, real.bundleDigest,
            real.motion, real.sampleSequence, real.observedAtNs)
        denied { a.withFrameWrite(copy) {} }
    }
    @Test fun everyRevocationReasonFencesComputedAndQueuedFrames() {
        for (reason in listOf("operator_cancelled", "manual_control", "physical_stop", "usb_disconnected",
            "usb_reconnect", "operator_backgrounded", "service_destroyed", "capability_changed")) {
            val a = authority(); leased(a); val t = ticket(a); a.revoke(reason)
            denied { a.withFrameWrite(t) { fail(reason) } }
            denied { a.withManualFrameWrite { fail(reason) } }
            assertNotNull(a.takeStopRequest())
        }
    }
    @Test fun queuedManualFramesCannotFollowFirstLeasedFrameOrCancellation() {
        val a = authority(); enable(a); val h = begin(a)
        a.withManualFrameWrite {} // Existing explicitly manual neutral operation while waiting.
        a.submit(h, raw()); a.withManualFrameWrite {} // No physical frame consumed yet.
        val t = ticket(a)
        denied { a.withManualFrameWrite { fail("manual frame crossed physical takeover") } }
        a.withFrameWrite(t) {}
        a.cancel(a.status().generation)
        a.takeStopRequest()
        denied { a.withManualFrameWrite {} }
        a.resetForExplicitManualOperation()
        a.withManualFrameWrite {}
        assertNull(a.beginFrame(operation, qualified.digest, 2, now, true))
    }
    @Test fun failureAfterFrameStartRevokesAndLateFailureCannotCancelReplacement() {
        val a = authority(); leased(a); val t = ticket(a)
        a.failFrame(t) // Same path for invalid complete-feedback, inference, or calibration error.
        assertEquals("cancelled", a.status().state)
        assertNotNull(a.takeStopRequest())
        a.resetForExplicitManualOperation(); enable(a); begin(a); a.failFrame(t)
        assertEquals("pending", a.status().state)
    }
    @Test fun incompleteFeedbackCannotEnterPhysicalFrameEvenWithFreshTimestamp() {
        val a = authority(); leased(a)
        denied { a.beginFrame(operation, qualified.digest, 1, now, false) }
        assertEquals("physical_intent_requires_complete_feedback", a.status().reason)
        assertNotNull(a.takeStopRequest())
    }
    @Test fun clockFailureAtPublicationCannotExtendOriginalDeadline() {
        val a = authority(); enable(a); val h = begin(a)
        assertEquals("expired", a.submit(h, raw()) { now += PhysicalIntentAuthority.AUTHORITY_NS; true }.state)
        assertNotNull(a.takeStopRequest())
    }
    @Test fun dispatchExceptionConsumesTicketAndRequestsStop() {
        val a = authority(); leased(a); val t = ticket(a)
        denied { a.withFrameWrite(t) { error("sink claim failed") } }
        assertNotNull(a.takeStopRequest())
        denied { a.withFrameWrite(t) {} }
    }
    @Test fun malformedOutOfBoundsAndUnrequestedAuthorityFieldsAreRejected() {
        val cases = listOf(raw(1001), raw().replace("0.02", "0.05"), raw().replace("0.08", "NaN"),
            raw().dropLast(1) + ",\"allow_physical\":true}", raw().replace("\"body\"", "\"world\""),
            raw().dropLast(1) + ",\"duration_ms\":100}")
        for (bad in cases) {
            val a = authority(); enable(a); assertEquals("cancelled", a.submit(begin(a), bad).state)
            assertNotNull(a.takeStopRequest()); assertNull(a.status().command)
        }
    }
    @Test fun clarificationUnsupportedAndModelStopNeverBecomeMotion() {
        for (raw in listOf("""{"schema_version":1,"kind":"clarify","question":"Which direction?"}""",
            """{"schema_version":1,"kind":"unsupported","reason":"No goal localization"}""",
            """{"schema_version":1,"kind":"stop_request"}""")) {
            val a = authority(); enable(a); assertEquals("cancelled", a.submit(begin(a), raw).state)
            assertNotNull(a.takeStopRequest()); assertNull(a.status().command)
        }
    }
    @Test fun clockRollbackClosesAuthorityAndCannotBeReenabled() {
        val a = authority(); leased(a); now--
        assertEquals("blocked", a.status().state)
        assertNotNull(a.takeStopRequest()); denied { enable(a) }
    }
    @Test fun closeFencesPendingAndFrameWork() {
        val a = authority(); leased(a); val t = ticket(a); a.close()
        denied { a.withFrameWrite(t) {} }; denied { enable(a) }
        assertNotNull(a.takeStopRequest())
    }
    @Test fun shadowOnlyCancellationCannotStopPhysicalOwnershipButSharedLifecycleCan() {
        val a = authority(); leased(a)
        sharedPhysicalRevocation(ShadowCancelReason.OPERATOR_CANCEL)?.let(a::revoke)
        assertEquals("leased", a.status().state)
        assertNull(a.takeStopRequest())
        for (reason in ShadowCancelReason.entries.filter { it != ShadowCancelReason.OPERATOR_CANCEL })
            assertNotNull(sharedPhysicalRevocation(reason))
        a.revoke(requireNotNull(sharedPhysicalRevocation(ShadowCancelReason.OPERATOR_BACKGROUNDED)))
        assertEquals("cancelled", a.status().state)
        assertNotNull(a.takeStopRequest())
    }

    @Test fun finalClaimAndRevocationHaveOneAtomicOrder() {
        val a = authority(); leased(a); val t = ticket(a)
        val entered = CountDownLatch(1); val release = CountDownLatch(1); val revoked = CountDownLatch(1)
        val sending = thread { a.withFrameWrite(t) { entered.countDown(); assertTrue(release.await(2, TimeUnit.SECONDS)) } }
        assertTrue(entered.await(1, TimeUnit.SECONDS))
        val cancelling = thread { a.revoke("manual_control"); revoked.countDown() }
        assertFalse(revoked.await(50, TimeUnit.MILLISECONDS))
        release.countDown(); sending.join(1000); cancelling.join(1000)
        assertEquals(0L, revoked.count); assertNotNull(a.takeStopRequest())
    }
    companion object {
        private val assets = File("src/main/assets")
        private fun binding(promoted: Boolean, transport: Boolean): PhysicalPolicyBinding {
            val metadata = JSONObject(File(assets, "policy_metadata.json").readText())
                .put("promoted", promoted).put("physical_transport_verified", transport).toString().toByteArray()
            return LoadedPolicyAssets.fromBytes(metadata, File(assets, "policy_android_manifest.json").readBytes(),
                File(assets, "policy_actor.onnx").readBytes(), File(assets, "stride_reference.json").readBytes(),
                File(assets, "assembly-four-leg-linkage-12dof.calibration.json").readBytes()).physicalBinding()
        }
        private val qualified by lazy { binding(true, true) }
        private val unqualified by lazy { binding(false, false) }
    }
}
