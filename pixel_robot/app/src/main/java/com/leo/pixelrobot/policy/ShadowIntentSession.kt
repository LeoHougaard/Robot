package com.leo.pixelrobot.policy

import java.security.MessageDigest
import java.util.Collections
import java.util.IdentityHashMap
import java.util.UUID
import org.json.JSONObject

/** Intent-only values. None of these types can express arming, permissions or assets. */
sealed class ShadowIntent {
    data class Velocity(val forward: Double, val lateral: Double, val yawRate: Double, val durationMs: Int) : ShadowIntent()
    data class Clarify(val question: String) : ShadowIntent()
    data class Unsupported(val reason: String) : ShadowIntent()
    object Stop : ShadowIntent()
}

/** Trusted-host byte binding. These are metadata claims, never physical attestation. */
class ShadowCapabilities private constructor(
    private val contract: PolicyContract,
    private val bounds: List<Pair<Double, Double>>,
    val digest: String,
    assetHashes: Map<String, String>,
    val maxDurationMs: Int,
    val requiresUnqualifiedOptIn: Boolean,
) {
    val assetSha256: Map<String, String> = Collections.unmodifiableMap(HashMap(assetHashes))
    internal fun accepts(intent: ShadowIntent.Velocity): Boolean {
        val values = listOf(intent.forward, intent.lateral, intent.yawRate)
        if (values.indices.any { values[it] !in bounds[it].first..bounds[it].second }) return false
        contract.requireRequest(intent.forward.toFloat(), intent.lateral.toFloat(), intent.yawRate.toFloat())
        return true
    }
    companion object {
        fun fromBundle(metadata: ByteArray, manifest: ByteArray, actor: ByteArray,
                       reference: ByteArray, calibration: ByteArray, maxDurationMs: Int = 1_000): ShadowCapabilities {
            require(maxDurationMs in 1..5_000)
            val meta = JSONObject(metadata.toString(Charsets.UTF_8))
            val index = JSONObject(manifest.toString(Charsets.UTF_8))
            require(meta.get("schema_version") == 2 && index.get("schema_version") == 2)
            val contract = PolicyContract.parse(metadata.toString(Charsets.UTF_8))
            require(contract.observationBuilder == "current_body_v22_428")
            // Existing software bundle compatibility check; this grants no
            // permission to activate hardware despite the historical name.
            contract.requireLiveActivationCandidate()
            require(meta.get("promoted") is Boolean && meta.get("physical_transport_verified") is Boolean)
            require(index.getString("profile_id") == contract.profileId)
            require(index.getString("profile_sha256") == contract.profileSha256)
            require(index.getString("source_weights_sha256") == contract.weightsSha256)
            require(index.getString("onnx_sha256") == actor.shadowSha256())
            require(meta.getString("reference_sha256") == reference.shadowSha256())
            require(contract.strideReferenceSha256 == reference.shadowSha256())
            contract.requireCalibration(RobotCalibration.parse(calibration.toString(Charsets.UTF_8)))
            PolicyFrameSession(contract, reference) // Validate actual reference geometry/coordinate contract.
            val hashes = sortedMapOf("policy_metadata.json" to metadata.shadowSha256(),
                "policy_android_manifest.json" to manifest.shadowSha256(), "policy_actor.onnx" to actor.shadowSha256(),
                "stride_reference.json" to reference.shadowSha256(), "calibration" to calibration.shadowSha256())
            val limits = meta.getJSONObject("validated_command_limits")
            val bounds = listOf("forward_m_s", "lateral_m_s", "yaw_rate_rad_s").map { name ->
                val pair = limits.getJSONArray(name)
                require(pair.length() == 2)
                val low = pair.getDouble(0); val high = pair.getDouble(1)
                require(low.isFinite() && high.isFinite() && low <= 0.0 && high >= 0.0 && low <= high)
                low to high
            }
            val identity = "pixel-shadow-capabilities-v1\n" + hashes.entries.joinToString("\n") { "${it.key}:${it.value}" } + "\n$maxDurationMs"
            return ShadowCapabilities(contract, bounds, identity.toByteArray().shadowSha256(), hashes.toMap(), maxDurationMs,
                !(meta.getBoolean("promoted") && meta.getBoolean("physical_transport_verified")))
        }
    }
}

class ShadowEnvelope internal constructor(
    val sessionId: String, val generation: Long, val sourceId: String, val sourceGeneration: Long,
    val requestId: String, val issuedAtMs: Long, val expiresAtMs: Long, val capabilityDigest: String,
)

/** A trusted host call issues this per-request SHADOW grant. Never physical permission. */
class ShadowGrant internal constructor(
    internal val envelope: ShadowEnvelope, val expiresAtMs: Long, val allowUnqualifiedMetadata: Boolean,
)

enum class ShadowState { SHADOW_ACCEPTED, CLARIFY, UNSUPPORTED, STOP_REQUESTED, REJECTED, CANCELLED, EXPIRED }

data class ShadowOutcome(
    val state: ShadowState, val requestId: String?, val reason: String,
    val intent: ShadowIntent? = null, val expiresAtMs: Long? = null,
) { val physicalExecution = false; val physicalStopConfirmed = false }

/** One-use compute/publication token. This is NOT an authorization to call PolicyOperation.send. */
class ShadowFrameTicket internal constructor(
    internal val envelope: ShadowEnvelope, val sampleSequence: Long, val observedAtMs: Long,
    val expiresAtMs: Long, internal val motion: MotionRequest,
)

data class ShadowMotionDiagnostic internal constructor(
    val requestId: String, val capabilityDigest: String, val sampleSequence: Long,
    val motion: MotionRequest,
) { val mode = "SHADOW"; val physicalExecution = false; val physicalStopConfirmed = false }

/**
 * Pure, in-memory Android-host admission. No controller, transport, network or timer.
 * Call poll from the owning host; actuator integrations still require separate reviewed
 * operator authority and independent final-write/stop/watchdog enforcement.
 * All state transitions are synchronized; slow computation happens outside this class.
 */
class ShadowIntentSession(
    initialCapabilities: ShadowCapabilities,
    private val clockMs: () -> Long,
    allowShadow: Boolean = false,
    private val maxSampleAgeMs: Long = 40,
    private val maxRequests: Int = 1_000,
) {
    val sessionId = UUID.randomUUID().toString()
    var capabilities = initialCapabilities
        private set
    private var generation = 0L
    private var counter = 0
    private var lastNow = 0L
    private var stopped = false
    private var closed = false
    private val sourceGenerations = mutableMapOf<String, Long>()
    private val requests = linkedMapOf<String, ShadowEnvelope>()
    private val outcomes = mutableMapOf<String, ShadowOutcome>()
    private val grants = IdentityHashMap<ShadowGrant, Boolean>()
    private var pendingFrame: ShadowFrameTicket? = null
    private var lastSampleSequence = -1L
    private var lastSampleTime = -1L

    init { require(allowShadow); require(maxSampleAgeMs in 1..1_000); require(maxRequests in 1..10_000) }

    private fun now(): Long {
        val next = try { clockMs() } catch (_: Throwable) { -1L }
        if (next < lastNow || next < 0) {
            stopped = true; generation += 1; cancelMatching(null, "host_clock_failed")
            error("host_monotonic_clock_failed")
        }
        lastNow = next
        return next
    }

    private fun ready() { check(!closed && !stopped) { "shadow_session_closed_or_stopped" } }
    private fun current(envelope: ShadowEnvelope) = requests[envelope.requestId] === envelope &&
        envelope.sessionId == sessionId && envelope.generation == generation &&
        envelope.sourceGeneration == (sourceGenerations[envelope.sourceId] ?: 0L) &&
        envelope.capabilityDigest == capabilities.digest

    private fun record(envelope: ShadowEnvelope, state: ShadowState, reason: String,
                       intent: ShadowIntent? = null, expiry: Long? = null): ShadowOutcome {
        if (state != ShadowState.SHADOW_ACCEPTED && pendingFrame?.envelope === envelope) pendingFrame = null
        return ShadowOutcome(state, envelope.requestId, reason, intent, expiry).also { outcomes[envelope.requestId] = it }
    }

    private fun expire(at: Long) {
        requests.values.forEach { envelope ->
            val outcome = outcomes[envelope.requestId]
            if ((outcome == null || outcome.state == ShadowState.SHADOW_ACCEPTED) &&
                at >= (outcome?.expiresAtMs ?: envelope.expiresAtMs)) {
                record(envelope, ShadowState.EXPIRED, "shadow_lease_expired", outcome?.intent)
            }
        }
    }

    private fun cancelMatching(sourceId: String?, reason: String) {
        requests.values.forEach { envelope ->
            val prior = outcomes[envelope.requestId]
            if ((sourceId == null || sourceId == envelope.sourceId) && (prior == null || prior.state == ShadowState.SHADOW_ACCEPTED))
                record(envelope, ShadowState.CANCELLED, reason, prior?.intent)
        }
    }

    @Synchronized fun beginRequest(sourceId: String, ttlMs: Long): ShadowEnvelope {
        val at = now(); ready(); expire(at)
        require(sourceId.isNotBlank() && sourceId.length <= 100)
        require(ttlMs in 1..60_000 && at <= Long.MAX_VALUE - ttlMs)
        check(counter < maxRequests) { "shadow_request_limit" }
        // Supervisor semantics: newer pending work wins; an active lease must
        // be explicitly cancelled by the trusted host before replacement.
        requests.values.filter { it.sourceId == sourceId && outcomes[it.requestId] == null }.forEach {
            record(it, ShadowState.CANCELLED, "superseded_pending_request")
        }
        counter += 1
        return ShadowEnvelope(sessionId, generation, sourceId, sourceGenerations[sourceId] ?: 0L,
            "r$counter", at, at + ttlMs, capabilities.digest).also { requests[it.requestId] = it }
    }

    @Synchronized fun grantFor(envelope: ShadowEnvelope, allowUnqualifiedMetadata: Boolean = false,
                               expiresAtMs: Long = envelope.expiresAtMs): ShadowGrant {
        val at = now(); ready(); expire(at)
        require(current(envelope) && outcomes[envelope.requestId] == null)
        require(expiresAtMs > at && expiresAtMs <= envelope.expiresAtMs)
        check(grants.size < maxRequests) { "shadow_grant_limit" }
        return ShadowGrant(envelope, expiresAtMs, allowUnqualifiedMetadata).also { grants[it] = true }
    }

    @Synchronized fun submitRaw(envelope: ShadowEnvelope, raw: String, grant: ShadowGrant? = null): ShadowOutcome {
        val at = now(); expire(at)
        if (requests[envelope.requestId] !== envelope) return ShadowOutcome(ShadowState.REJECTED, null, "unknown_host_envelope")
        if (outcomes.containsKey(envelope.requestId)) return ShadowOutcome(ShadowState.REJECTED, envelope.requestId, "request_already_resolved")
        val intent = try { ShadowIntentDecoder.parse(raw) } catch (_: IllegalArgumentException) {
            return record(envelope, ShadowState.REJECTED, "strict_intent_contract_failed")
        }
        return submit(envelope, intent, grant)
    }

    @Synchronized fun submit(envelope: ShadowEnvelope, intent: ShadowIntent, grant: ShadowGrant? = null): ShadowOutcome {
        val at = now(); expire(at)
        if (requests[envelope.requestId] !== envelope) return ShadowOutcome(ShadowState.REJECTED, null, "unknown_host_envelope")
        if (outcomes.containsKey(envelope.requestId)) return ShadowOutcome(ShadowState.REJECTED, envelope.requestId, "request_already_resolved")
        if (closed || stopped || !current(envelope)) return record(envelope, ShadowState.REJECTED, "stale_host_envelope")
        if (!validIntent(intent)) return record(envelope, ShadowState.REJECTED, "strict_intent_contract_failed")
        when (intent) {
            ShadowIntent.Stop -> { stop(); return record(envelope, ShadowState.STOP_REQUESTED, "model_requested_shadow_stop", intent) }
            is ShadowIntent.Clarify -> return record(envelope, ShadowState.CLARIFY, "clarification_required", intent)
            is ShadowIntent.Unsupported -> return record(envelope, ShadowState.UNSUPPORTED, "unsupported_request", intent)
            is ShadowIntent.Velocity -> {
                if (grant == null || grants[grant] != true || grant.envelope !== envelope || at >= grant.expiresAtMs)
                    return record(envelope, ShadowState.REJECTED, "current_host_shadow_grant_required", intent)
                if (capabilities.requiresUnqualifiedOptIn && !grant.allowUnqualifiedMetadata)
                    return record(envelope, ShadowState.REJECTED, "unqualified_shadow_opt_in_required", intent)
                if (!capabilities.accepts(intent))
                    return record(envelope, ShadowState.REJECTED, "outside_active_command_bounds", intent)
                if (intent.durationMs > capabilities.maxDurationMs || at > Long.MAX_VALUE - intent.durationMs)
                    return record(envelope, ShadowState.REJECTED, "duration_outside_shadow_limit", intent)
                val expiry = at + intent.durationMs
                if (expiry > minOf(envelope.expiresAtMs, grant.expiresAtMs))
                    return record(envelope, ShadowState.REJECTED, "duration_outlives_original_deadline", intent)
                if (outcomes.values.any { it.state == ShadowState.SHADOW_ACCEPTED })
                    return record(envelope, ShadowState.REJECTED, "another_request_owns_shadow_lease", intent)
                return record(envelope, ShadowState.SHADOW_ACCEPTED, "compute_only_not_physical_permission", intent, expiry)
            }
        }
    }

    @Synchronized fun poll() { pollAt() }
    /** The owning host may use the exact clock-checked poll time for an earlier provider deadline. */
    @Synchronized internal fun pollAt(): Long = now().also { expire(it) }
    /** One clock-checked view: callers must not publish an outcome retained before expiry polling. */
    @Synchronized internal fun pollOutcome(requestId: String): Pair<Long, ShadowOutcome?> {
        val at = now(); expire(at)
        return at to outcomes[requestId]
    }
    @Synchronized fun outcome(requestId: String): ShadowOutcome? { expire(now()); return outcomes[requestId] }
    @Synchronized fun cancelSource(sourceId: String) {
        now(); require(sourceId.isNotBlank() && sourceId.length <= 100)
        if (requests.values.any { it.sourceId == sourceId })
            sourceGenerations[sourceId] = (sourceGenerations[sourceId] ?: 0L) + 1
        cancelMatching(sourceId, "source_cancelled")
    }
    @Synchronized fun stop(): ShadowOutcome {
        now(); stopped = true; generation += 1; cancelMatching(null, "trusted_shadow_stop")
        return ShadowOutcome(ShadowState.STOP_REQUESTED, null, "physical_stop_unconfirmed")
    }
    @Synchronized fun close() {
        // Closing remains possible after a bad injected host clock.
        closed = true; stopped = true; generation += 1; cancelMatching(null, "shadow_session_closed")
    }
    @Synchronized fun replaceCapabilities(next: ShadowCapabilities) {
        now(); ready()
        if (next.digest != capabilities.digest) { generation += 1; cancelMatching(null, "capability_changed"); capabilities = next }
    }

    @Synchronized fun beginFrame(sampleSequence: Long, observedAtMs: Long): ShadowFrameTicket {
        val at = now(); ready(); expire(at)
        check(pendingFrame == null) { "shadow_frame_already_in_flight" }
        val active = outcomes.values.singleOrNull { it.state == ShadowState.SHADOW_ACCEPTED }
            ?: error("no_current_shadow_lease")
        val envelope = requests.getValue(requireNotNull(active.requestId))
        if (sampleSequence < 0 || sampleSequence <= lastSampleSequence || observedAtMs < 0 ||
            observedAtMs <= lastSampleTime || observedAtMs > at || at - observedAtMs > maxSampleAgeMs) {
            cancelSource(envelope.sourceId); error("stale_duplicate_or_future_sample")
        }
        val intent = active.intent as ShadowIntent.Velocity
        val motion = MotionRequest(forward = intent.forward.toFloat(), lateral = intent.lateral.toFloat(),
            yawRate = intent.yawRate.toFloat(), heightOffset = 0f, roll = 0f, pitch = 0f)
        lastSampleSequence = sampleSequence; lastSampleTime = observedAtMs
        return ShadowFrameTicket(envelope, sampleSequence, observedAtMs, requireNotNull(active.expiresAtMs), motion)
            .also { pendingFrame = it }
    }

    @Synchronized fun finishFrame(ticket: ShadowFrameTicket): ShadowMotionDiagnostic {
        val at = now(); expire(at)
        check(ticket === pendingFrame) { "shadow_frame_cancelled_or_stale" }
        pendingFrame = null
        val envelope = ticket.envelope
        if (closed || stopped || !current(envelope) || outcomes[envelope.requestId]?.state != ShadowState.SHADOW_ACCEPTED ||
            at >= ticket.expiresAtMs || at - ticket.observedAtMs > maxSampleAgeMs) {
            cancelSource(envelope.sourceId); error("shadow_frame_cancelled_stale_or_expired")
        }
        return ShadowMotionDiagnostic(envelope.requestId, envelope.capabilityDigest, ticket.sampleSequence, ticket.motion)
    }
    @Synchronized fun failFrame(ticket: ShadowFrameTicket) {
        if (ticket === pendingFrame) cancelSource(ticket.envelope.sourceId)
    }
}

internal fun validIntent(intent: ShadowIntent): Boolean = when (intent) {
    is ShadowIntent.Velocity -> listOf(intent.forward, intent.lateral, intent.yawRate).all { it.isFinite() } && intent.durationMs in 1..5_000
    is ShadowIntent.Clarify -> validText(intent.question)
    is ShadowIntent.Unsupported -> validText(intent.reason)
    ShadowIntent.Stop -> true
}

internal fun validText(value: String): Boolean = value.any { !pythonWhitespace(it) } && value.codePointCount(0, value.length) <= 500 && validUnicode(value)
private fun pythonWhitespace(char: Char): Boolean = char in '\u0009'..'\u000d' || char in '\u001c'..'\u0020' ||
    char == '\u0085' || char == '\u00a0' || char == '\u1680' || char in '\u2000'..'\u200a' ||
    char == '\u2028' || char == '\u2029' || char == '\u202f' || char == '\u205f' || char == '\u3000'
internal fun validUnicode(value: String): Boolean {
    var index = 0
    while (index < value.length) {
        val char = value[index++]
        if (char.isHighSurrogate()) {
            if (index == value.length || !value[index++].isLowSurrogate()) return false
        } else if (char.isLowSurrogate()) return false
    }
    return true
}
private fun ByteArray.shadowSha256(): String = MessageDigest.getInstance("SHA-256").digest(this).joinToString("") { "%02x".format(it) }
