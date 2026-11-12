package com.leo.pixelrobot.policy

import java.io.Closeable
import org.json.JSONObject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/** A read-only provider envelope, never a motion request or operator grant. Identity matters. */
class ShadowRequestHandle internal constructor(
    val text: String,
    val requestId: String,
    val expiresAtMs: Long,
    val capabilityDigest: String,
) {
    val providerResultExpiresAtMs: Long = expiresAtMs -
        (ShadowRequestCoordinator.REQUEST_TTL_MS - ShadowRequestCoordinator.PROVIDER_RESULT_TTL_MS)
}

enum class ShadowServiceState { IDLE, PENDING, SHADOW_ACCEPTED, CLARIFY, UNSUPPORTED, STOP_REQUESTED, REJECTED, CANCELLED, EXPIRED, UNAVAILABLE, CLOSED }
enum class ShadowCancelReason { OPERATOR_CANCEL, MANUAL_CONTROL, OPERATOR_BACKGROUNDED, USB_DISCONNECTED, USB_RECONNECT, PHYSICAL_STOP, SERVICE_DESTROYED }

/** Admission-only preview. It deliberately contains no physical MotionRequest or actuator target. */
data class ShadowIntentPreview(val forward: Double, val lateral: Double, val yawRate: Double, val durationMs: Int)

data class ShadowServiceStatus(
    val state: ShadowServiceState = ShadowServiceState.IDLE,
    val reason: String = "explicit_operator_shadow_request_required",
    val requestId: String? = null,
    val expiresAtMs: Long? = null,
    val capabilityDigest: String? = null,
    val assetSha256: Map<String, String> = emptyMap(),
    val preview: ShadowIntentPreview? = null,
    val question: String? = null,
    val unsupportedReason: String? = null,
    val requiresUnqualifiedOptIn: Boolean = true,
) {
    val physicalExecution = false
    val physicalStopConfirmed = false
    val policyActorInference = false
    fun toJson(): JSONObject = JSONObject()
        .put("mode", "SHADOW")
        .put("state", state.name.lowercase())
        .put("reason", reason)
        .put("request_id", requestId ?: JSONObject.NULL)
        .put("expires_at_ms", expiresAtMs ?: JSONObject.NULL)
        .put("capability_digest", capabilityDigest ?: JSONObject.NULL)
        .put("asset_sha256", JSONObject(assetSha256))
        .put("requires_unqualified_opt_in", requiresUnqualifiedOptIn)
        .put("physical_execution", physicalExecution)
        .put("physical_stop_confirmed", physicalStopConfirmed)
        .put("policy_actor_inference", policyActorInference)
        .put("question", question ?: JSONObject.NULL)
        .put("unsupported_reason", unsupportedReason ?: JSONObject.NULL)
        .put("preview", preview?.let { JSONObject()
            .put("forward_m_s", it.forward).put("lateral_m_s", it.lateral).put("yaw_rate_rad_s", it.yawRate)
            .put("duration_ms", it.durationMs).put("height_offset_m", 0).put("roll_rad", 0).put("pitch_rad", 0)
        } ?: JSONObject.NULL)
}

/**
 * Service-owned pure admission controller. No network, timer, USB, controller, actor or motor sink.
 * Only beginOperatorRequest can create a host grant. Model completion has the opaque original
 * handle and intent bytes, and cannot renew the original deadline. The service polls independently.
 */
class ShadowRequestCoordinator(
    private val capabilities: ShadowCapabilities,
    private val clockMs: () -> Long,
) : Closeable {
    private var session = newSession()
    private var sessionStopped = false
    private var closed = false
    private data class Pending(val handle: ShadowRequestHandle, val envelope: ShadowEnvelope, val grant: ShadowGrant)
    private var current: Pending? = null
    private val mutableUpdates = MutableStateFlow(baseStatus())
    val updates: StateFlow<ShadowServiceStatus> = mutableUpdates.asStateFlow()
    private var snapshot: ShadowServiceStatus
        get() = mutableUpdates.value
        set(value) { mutableUpdates.value = value }

    private fun newSession() = ShadowIntentSession(capabilities, clockMs, allowShadow = true)
    private fun baseStatus() = ShadowServiceStatus(capabilityDigest = capabilities.digest,
        assetSha256 = capabilities.assetSha256, requiresUnqualifiedOptIn = capabilities.requiresUnqualifiedOptIn)

    val status: ShadowServiceStatus @Synchronized get() = snapshot
    val currentHandle: ShadowRequestHandle? @Synchronized get() = current?.handle

    /** Trusted operator action, never called by a provider completion. Each action grants only SHADOW. */
    @Synchronized fun beginOperatorRequest(text: String, allowUnqualifiedMetadata: Boolean): ShadowRequestHandle {
        check(!closed) { "shadow_coordinator_closed" }
        require(text.isNotBlank() && validUnicode(text) && text.codePointCount(0, text.length) <= MAX_TEXT_CODEPOINTS) {
            "shadow_text_must_be_nonblank_and_at_most_2000_codepoints"
        }
        try {
            // A fresh explicit request revokes the earlier source, including an admitted lease.
            if (sessionStopped) { session.close(); session = newSession(); sessionStopped = false }
            session.cancelSource(OPERATOR_SOURCE)
            val envelope = session.beginRequest(OPERATOR_SOURCE, REQUEST_TTL_MS)
            val grant = session.grantFor(envelope, allowUnqualifiedMetadata)
            val handle = ShadowRequestHandle(text, "${envelope.sessionId}:${envelope.requestId}", envelope.expiresAtMs, envelope.capabilityDigest)
            current = Pending(handle, envelope, grant)
            snapshot = baseStatus().copy(state = ShadowServiceState.PENDING, reason = "awaiting_original_complete_provider_intent",
                requestId = handle.requestId, expiresAtMs = handle.providerResultExpiresAtMs)
            return handle
        } catch (_: Exception) {
            unavailable()
            error("shadow_clock_or_session_failed")
        }
    }

    @Synchronized fun submitResponse(handle: ShadowRequestHandle, originalCompleteIntentJson: String,
                                     claimPublication: () -> Boolean = { true }): ShadowServiceStatus {
        val pending = current ?: return snapshot
        if (closed || pending.handle !== handle || snapshot.state != ShadowServiceState.PENDING) return snapshot
        // Preserve expiry at the original deadline even if the independent timer has not ticked.
        poll()
        if (snapshot.state != ShadowServiceState.PENDING) return snapshot
        try {
            // Under the host lock, the provider must atomically win publication against its
            // timeout/cancellation. Provider callbacks never enter this lock while holding
            // their own lock, so this host -> provider claim has no lock-order inversion.
            if (!claimPublication()) {
                session.cancelSource(OPERATOR_SOURCE)
                snapshot = baseStatus().copy(state = ShadowServiceState.CANCELLED,
                    reason = "provider_publication_cancelled", requestId = handle.requestId)
                return snapshot
            }
            val outcome = session.submitRaw(pending.envelope, originalCompleteIntentJson, pending.grant)
            if (outcome.state == ShadowState.STOP_REQUESTED) sessionStopped = true
            // Count parsing/admission and claim time too; claiming cannot renew the host cutoff.
            val (publishedAt, currentOutcome) = session.pollOutcome(pending.envelope.requestId)
            if (!expireProvider(pending, publishedAt)) publish(currentOutcome ?: outcome, handle)
        } catch (_: Exception) { unavailable() }
        return snapshot
    }

    @Synchronized fun failRequest(handle: ShadowRequestHandle): ShadowServiceStatus {
        val pending = current ?: return snapshot
        if (closed || pending.handle !== handle || snapshot.state != ShadowServiceState.PENDING) return snapshot
        poll()
        if (snapshot.state == ShadowServiceState.PENDING) {
            session.cancelSource(OPERATOR_SOURCE)
            snapshot = baseStatus().copy(state = ShadowServiceState.REJECTED, reason = "provider_request_failed",
                requestId = handle.requestId)
        }
        return snapshot
    }

    @Synchronized fun poll(): ShadowServiceStatus {
        if (closed) return snapshot
        try {
            val pending = current
            if (pending == null) session.poll()
            else if (snapshot.state == ShadowServiceState.PENDING || snapshot.state == ShadowServiceState.SHADOW_ACCEPTED) {
                val (at, outcome) = session.pollOutcome(pending.envelope.requestId)
                if (outcome != null) publish(outcome, pending.handle)
                else if (snapshot.state == ShadowServiceState.PENDING) expireProvider(pending, at)
            }
        } catch (_: Exception) {
            unavailable()
        }
        return snapshot
    }

    @Synchronized fun cancel(reason: ShadowCancelReason = ShadowCancelReason.OPERATOR_CANCEL): ShadowServiceStatus {
        if (closed) return snapshot
        // Cancellation remains possible even when the monotonic clock has failed.
        runCatching { session.cancelSource(OPERATOR_SOURCE) }.onFailure { session.close(); sessionStopped = true }
        val id = current?.handle?.requestId
        current = null
        snapshot = baseStatus().copy(state = ShadowServiceState.CANCELLED, reason = reason.name.lowercase(), requestId = id)
        return snapshot
    }

    @Synchronized fun stop(): ShadowServiceStatus {
        if (closed) return snapshot
        runCatching { session.stop() }.onFailure { session.close() }
        sessionStopped = true
        val id = current?.handle?.requestId
        current = null
        snapshot = baseStatus().copy(state = ShadowServiceState.STOP_REQUESTED,
            reason = "shadow_preview_stopped_physical_stop_unconfirmed", requestId = id)
        return snapshot
    }

    @Synchronized override fun close() {
        session.close(); current = null; closed = true
        snapshot = baseStatus().copy(state = ShadowServiceState.CLOSED, reason = "shadow_service_closed")
    }

    private fun expireProvider(pending: Pending, at: Long): Boolean {
        if (at < pending.handle.providerResultExpiresAtMs) return false
        session.cancelSource(OPERATOR_SOURCE)
        snapshot = baseStatus().copy(state = ShadowServiceState.EXPIRED,
            reason = "provider_result_deadline_elapsed", requestId = pending.handle.requestId)
        return true
    }

    private fun unavailable() {
        session.close(); sessionStopped = true; current = null
        snapshot = baseStatus().copy(state = ShadowServiceState.UNAVAILABLE, reason = "shadow_clock_or_session_failed")
    }

    private fun publish(outcome: ShadowOutcome, handle: ShadowRequestHandle) {
        val accepted = outcome.intent as? ShadowIntent.Velocity
        snapshot = baseStatus().copy(state = ShadowServiceState.valueOf(outcome.state.name), reason = outcome.reason,
            requestId = handle.requestId, expiresAtMs = outcome.expiresAtMs,
            preview = if (outcome.state == ShadowState.SHADOW_ACCEPTED && accepted != null)
                ShadowIntentPreview(accepted.forward, accepted.lateral, accepted.yawRate, accepted.durationMs) else null,
            question = (outcome.intent as? ShadowIntent.Clarify)?.question,
            unsupportedReason = (outcome.intent as? ShadowIntent.Unsupported)?.reason)
    }

    companion object {
        const val REQUEST_TTL_MS = 10_000L
        const val PROVIDER_RESULT_TTL_MS = 8_000L
        const val MAX_TEXT_CODEPOINTS = 2_000
        private const val OPERATOR_SOURCE = "local_operator_shadow"
    }
}
