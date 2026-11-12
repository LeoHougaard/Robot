package com.leo.pixelrobot.policy

import java.io.File
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ShadowIntentSessionTest {
    private val assets = File("src/main/assets")
    private class Clock(var value: Long = 1_000) { fun now() = value }
    private fun capabilities(maxDuration: Int = 1_000, actor: ByteArray? = null, calibration: ByteArray? = null, metadata: ByteArray? = null): ShadowCapabilities =
        ShadowCapabilities.fromBundle(metadata ?: File(assets, "policy_metadata.json").readBytes(),
            File(assets, "policy_android_manifest.json").readBytes(), actor ?: File(assets, "policy_actor.onnx").readBytes(),
            File(assets, "stride_reference.json").readBytes(), calibration ?: File(assets, "assembly-four-leg-linkage-12dof.calibration.json").readBytes(), maxDuration)
    private fun session(clock: Clock = Clock(), caps: ShadowCapabilities = capabilities(), maximum: Int = 1_000) =
        ShadowIntentSession(caps, clock::now, allowShadow = true, maxRequests = maximum)
    private fun velocity(duration: Int = 100) = ShadowIntent.Velocity(.02, 0.0, 0.0, duration)
    private fun admit(session: ShadowIntentSession, source: String = "operator", duration: Int = 100): ShadowEnvelope {
        val envelope = session.beginRequest(source, 2_000)
        val grant = session.grantFor(envelope, true)
        assertEquals(ShadowState.SHADOW_ACCEPTED, session.submit(envelope, velocity(duration), grant).state)
        return envelope
    }
    private fun fails(block: () -> Unit) { assertTrue(runCatching(block).isFailure) }

    @Test fun defaultsDoNotGrantEvenShadowAdmission() {
        fails { ShadowIntentSession(capabilities(), { 1_000L }) }
        val s = session(); val e = s.beginRequest("operator", 1_000)
        assertEquals(ShadowState.REJECTED, s.submit(e, velocity()).state)
    }

    @Test fun currentCandidateNeedsSeparateUnqualifiedGrant() {
        val s = session(); val e = s.beginRequest("operator", 1_000)
        assertEquals(ShadowState.REJECTED, s.submit(e, velocity(), s.grantFor(e)).state)
    }

    @Test fun sharedPythonKotlinIntentVectors() {
        val vectors = JSONObject(File("../../robot_llm/data/runtime_intent_vectors.json").readText()).getJSONArray("cases")
        assertEquals(32, vectors.length())
        for (index in 0 until vectors.length()) {
            val vector = vectors.getJSONObject(index)
            val s = session(); val e = s.beginRequest("operator", 2_000)
            val result = s.submitRaw(e, vector.getString("raw"), s.grantFor(e, true))
            assertEquals(vector.getString("name"), vector.getString("expected"), result.state.name.lowercase())
            assertFalse(result.physicalExecution); assertFalse(result.physicalStopConfirmed)
        }
    }

    @Test fun namedMotionRequestPreservesAxisOrderAndNeutralPosture() {
        val s = session(); val e = s.beginRequest("operator", 1_000)
        assertEquals(ShadowState.SHADOW_ACCEPTED, s.submit(e, ShadowIntent.Velocity(.03, -.01, .08, 100), s.grantFor(e, true)).state)
        val ticket = s.beginFrame(0, 1_000)
        val diagnostic = s.finishFrame(ticket)
        assertEquals(.03f, diagnostic.motion.forward, 0f)
        assertEquals(-.01f, diagnostic.motion.lateral, 0f)
        assertEquals(.08f, diagnostic.motion.yawRate, 0f)
        assertEquals(0f, diagnostic.motion.heightOffset, 0f)
        assertEquals(0f, diagnostic.motion.roll, 0f); assertEquals(0f, diagnostic.motion.pitch, 0f)
        assertEquals("SHADOW", diagnostic.mode); assertFalse(diagnostic.physicalExecution)
        fails { s.finishFrame(ticket) }
    }

    @Test fun copiedEnvelopeGrantAndTicketCannotForgeIdentity() {
        val s = session(); val e = s.beginRequest("operator", 1_000)
        val copied = ShadowEnvelope(e.sessionId, e.generation, e.sourceId, e.sourceGeneration, e.requestId, e.issuedAtMs, e.expiresAtMs, e.capabilityDigest)
        assertEquals(ShadowState.REJECTED, s.submit(copied, velocity(), s.grantFor(e, true)).state)
        val fakeGrant = ShadowGrant(e, e.expiresAtMs, true)
        assertEquals(ShadowState.REJECTED, s.submit(e, velocity(), fakeGrant).state)
        val next = admit(s)
        val ticket = s.beginFrame(0, 1_000)
        val copiedTicket = ShadowFrameTicket(next, ticket.sampleSequence, ticket.observedAtMs, ticket.expiresAtMs, ticket.motion)
        fails { s.finishFrame(copiedTicket) }
        s.finishFrame(ticket)
    }

    @Test fun grantsAreRequestAndSessionBound() {
        val a = session(); val b = session()
        val ea = a.beginRequest("operator", 1_000); val ga = a.grantFor(ea, true)
        val eb = b.beginRequest("operator", 1_000)
        assertEquals(ShadowState.REJECTED, b.submit(eb, velocity(), ga).state)
        a.cancelSource("operator")
        val next = a.beginRequest("operator", 1_000)
        assertEquals(ShadowState.REJECTED, a.submit(next, velocity(), ga).state)
    }

    @Test fun supersededPendingReplyCannotRevive() {
        val s = session(); val old = s.beginRequest("operator", 1_000); val grant = s.grantFor(old, true)
        val newer = s.beginRequest("operator", 1_000)
        assertEquals(ShadowState.CANCELLED, s.outcome(old.requestId)?.state)
        assertEquals(ShadowState.REJECTED, s.submit(old, velocity(), grant).state)
        assertEquals(ShadowState.SHADOW_ACCEPTED, s.submit(newer, velocity(), s.grantFor(newer, true)).state)
    }

    @Test fun activeSourceCannotBeImplicitlyPreempted() {
        val s = session(); val first = admit(s)
        val other = s.beginRequest("camera", 1_000)
        assertEquals(ShadowState.REJECTED, s.submit(other, velocity(), s.grantFor(other, true)).state)
        assertEquals(ShadowState.SHADOW_ACCEPTED, s.outcome(first.requestId)?.state)
        s.cancelSource("operator"); admit(s, "manual")
    }

    @Test fun remainingDurationAndGrantDeadlinesAreOriginalHostTimes() {
        val clock = Clock(); val s = session(clock); val e = s.beginRequest("operator", 100)
        val g = s.grantFor(e, true); clock.value += 1
        assertEquals(ShadowState.REJECTED, s.submit(e, velocity(), g).state)
        val another = s.beginRequest("operator", 1_000); val shorter = s.grantFor(another, true, clock.value + 20)
        assertEquals(ShadowState.REJECTED, s.submit(another, velocity(), shorter).state)
        val expired = s.beginRequest("operator", 1_000); val stale = s.grantFor(expired, true, clock.value + 20)
        clock.value += 20
        assertEquals(ShadowState.REJECTED, s.submit(expired, velocity(1), stale).state)
    }

    @Test fun exactLeaseDeadlineExpiresBeforePublishingComputedMotion() {
        val clock = Clock(); val s = session(clock); val e = admit(s)
        val ticket = s.beginFrame(0, clock.value); clock.value += 100
        fails { s.finishFrame(ticket) }
        assertEquals(ShadowState.EXPIRED, s.outcome(e.requestId)?.state)
    }

    @Test fun computeTimeCountsAgainstSampleFreshness() {
        val clock = Clock(); val s = session(clock); val e = admit(s)
        val ticket = s.beginFrame(0, clock.value); clock.value += 41
        fails { s.finishFrame(ticket) }
        assertEquals(ShadowState.CANCELLED, s.outcome(e.requestId)?.state)
    }

    @Test fun duplicateFutureAndOutOfOrderSamplesCancel() {
        listOf(1_001L, 959L, -1L).forEach { time ->
            val s = session(); val e = admit(s)
            fails { s.beginFrame(0, time) }
            assertEquals(ShadowState.CANCELLED, s.outcome(e.requestId)?.state)
        }
        val s = session(); admit(s); s.finishFrame(s.beginFrame(0, 1_000))
        fails { s.beginFrame(1, 1_000) }
    }

    @Test fun cancelFencesOldCompletionWithoutCancellingNewerTicket() {
        val clock = Clock(); val s = session(clock); admit(s)
        val old = s.beginFrame(0, clock.value)
        s.cancelSource("operator"); admit(s); clock.value += 20
        val fresh = s.beginFrame(1, clock.value)
        s.failFrame(old); fails { s.finishFrame(old) }
        s.finishFrame(fresh)
    }

    @Test fun capabilityChangeAndOldGrantNeverRevive() {
        val s = session(); val e = s.beginRequest("operator", 1_000); val g = s.grantFor(e, true)
        s.replaceCapabilities(capabilities(500))
        assertEquals(ShadowState.CANCELLED, s.outcome(e.requestId)?.state)
        assertEquals(ShadowState.REJECTED, s.submit(e, velocity(), g).state)
        admit(s); val ticket = s.beginFrame(0, 1_000)
        s.replaceCapabilities(capabilities(1_000)); fails { s.finishFrame(ticket) }
    }

    @Test fun trustedStopNeverWaitsForExternalComputationAndLatches() {
        val s = session(); admit(s); val ticket = s.beginFrame(0, 1_000)
        val release = CountDownLatch(1); val finished = CountDownLatch(1)
        var fenced = false
        val worker = thread {
            release.await(2, TimeUnit.SECONDS)
            fenced = runCatching { s.finishFrame(ticket) }.isFailure
            finished.countDown()
        }
        val stopped = s.stop()
        assertEquals(ShadowState.STOP_REQUESTED, stopped.state); assertFalse(stopped.physicalStopConfirmed)
        fails { s.beginRequest("operator", 1_000) }
        release.countDown(); assertTrue(finished.await(2, TimeUnit.SECONDS)); worker.join(); assertTrue(fenced)
    }

    @Test fun modelStopCloseAndClockFailureFenceFrames() {
        val s = session(); admit(s); val ticket = s.beginFrame(0, 1_000)
        val stop = s.beginRequest("stop-source", 1_000)
        assertEquals(ShadowState.STOP_REQUESTED, s.submit(stop, ShadowIntent.Stop).state)
        fails { s.finishFrame(ticket) }; fails { s.beginRequest("operator", 1_000) }
        val closed = session(); admit(closed); val other = closed.beginFrame(0, 1_000)
        closed.close(); fails { closed.finishFrame(other) }
        val clock = Clock(); val failed = session(clock); admit(failed)
        val last = failed.beginFrame(0, 1_000); clock.value = 999
        fails { failed.finishFrame(last) }; clock.value = 1_001
        fails { failed.beginRequest("operator", 1_000) }; failed.close()
    }

    @Test fun requestAndGrantCountsAreBounded() {
        val s = session(maximum = 1); val e = s.beginRequest("operator", 1_000)
        s.grantFor(e, true)
        fails { s.grantFor(e, true) }; fails { s.beginRequest("operator", 1_000) }
    }

    @Test fun rawDecoderIsIndependentAndRejectsExtraPrivilegeFields() {
        listOf("{\"schema_version\":1,\"kind\":\"stop_request\",\"session_id\":\"fake\"}",
            "{\"schema_version\":1,\"kind\":\"clarify\",\"question\":\"\\udc00\"}",
            "{\"schema_version\":1,\"kind\":\"clarify\",\"question\":\"${"x".repeat(501)}\"}",
            " ".repeat(16_385), "{\"schema_version\":1,\"kind\":\"stop_request\",}",
            "{\"schema_version\":true,\"kind\":\"stop_request\"}").forEach { raw -> fails { ShadowIntentDecoder.parse(raw) } }
        val emoji = "\uD83D\uDC3E".repeat(500)
        assertTrue(ShadowIntentDecoder.parse("{\"schema_version\":1,\"kind\":\"clarify\",\"question\":\"$emoji\"}") is ShadowIntent.Clarify)
    }

    @Test fun changedModelAndCalibrationBytesCannotMasqueradeAsSameBundle() {
        fails { capabilities(actor = byteArrayOf(1, 2, 3)) }
        val calibration = JSONObject(File(assets, "assembly-four-leg-linkage-12dof.calibration.json").readText())
        calibration.put("joint_coordinate_convention", "legacy_relative_knee")
        fails { capabilities(calibration = calibration.toString().toByteArray()) }
        val metadata = JSONObject(File(assets, "policy_metadata.json").readText()).put("checkpoint_epoch", 2749)
        fails { capabilities(metadata = metadata.toString().toByteArray()) }
        assertNotEquals(capabilities().digest, capabilities(500).digest)
    }

    @Test fun invalidTypedIntentsCannotBypassDecoderContract() {
        listOf(ShadowIntent.Velocity(Double.NaN, 0.0, 0.0, 100), ShadowIntent.Velocity(.02, 0.0, 0.0, 0),
            ShadowIntent.Clarify(""), ShadowIntent.Unsupported("\uD800")).forEach { intent ->
            val s = session(); val e = s.beginRequest("operator", 1_000)
            assertEquals(ShadowState.REJECTED, s.submit(e, intent, s.grantFor(e, true)).state)
        }
    }

    @Test fun capabilitiesDoNotExposeMutableCollectionsOrRuntimeArrays() {
        val caps = capabilities()
        val digest = caps.digest
        val actor = caps.assetSha256.getValue("policy_actor.onnx")
        fails { (caps.assetSha256 as MutableMap<String, String>)["policy_actor.onnx"] = "0".repeat(64) }
        assertEquals(digest, caps.digest)
        assertEquals(actor, caps.assetSha256.getValue("policy_actor.onnx"))
        assertFalse(ShadowCapabilities::class.java.methods.any { it.name == "getContract" || it.name == "getBounds" })
    }
}
