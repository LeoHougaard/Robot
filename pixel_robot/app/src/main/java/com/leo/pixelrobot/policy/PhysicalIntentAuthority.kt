package com.leo.pixelrobot.policy

import org.json.JSONObject
import java.security.MessageDigest
import java.util.Collections
import java.util.UUID

/** Byte identity and qualification claims, never a measurement or operator permission. */
class PhysicalPolicyBinding private constructor(
    internal val capabilities: ShadowCapabilities,
    val digest: String,
    blockers: List<String>,
) {
    val blockers: List<String> = Collections.unmodifiableList(ArrayList(blockers))
    val assetSha256: Map<String, String> get() = capabilities.assetSha256
    val eligible: Boolean get() = blockers.isEmpty()
    companion object {
        internal fun fromBundle(capabilities: ShadowCapabilities, metadata: ByteArray): PhysicalPolicyBinding {
            val value = JSONObject(metadata.toString(Charsets.UTF_8))
            val blockers = buildList {
                if (value.get("promoted") != true) add("policy_not_promoted: reviewed policy qualification required")
                if (value.get("physical_transport_verified") != true)
                    add("transport_not_verified: motor-disabled transport and watchdog acceptance required")
            }
            val identity = "pixel-physical-intent-v1\n" + capabilities.assetSha256.toSortedMap()
                .entries.joinToString("\n") { "${it.key}:${it.value}" }
            val digest = MessageDigest.getInstance("SHA-256").digest(identity.toByteArray())
                .joinToString("") { "%02x".format(it.toInt() and 255) }
            return PhysicalPolicyBinding(capabilities, digest, blockers)
        }
    }
}

/** Opaque host object, never accepted from JSON, the provider, or a shadow grant. */
class PhysicalRequestHandle internal constructor(val requestId: String, val expiresAtMs: Long,
                                                internal val providerDeadlineMs: Long)
class PhysicalFrameTicket internal constructor(
    internal val lease: Any, internal val generation: Long, internal val operation: Any,
    internal val bundleDigest: String, val motion: MotionRequest,
    val sampleSequence: Long, val observedAtNs: Long,
)

data class PhysicalIntentStatus(
    val state: String, val reason: String, val eligible: Boolean, val blockers: List<String>,
    val bundleDigest: String, val assetSha256: Map<String, String>, val generation: Long,
    val expiresAtMs: Long?, val requestId: String?, val command: ShadowIntent.Velocity?,
) {
    fun toJson(): JSONObject = JSONObject().put("state", state).put("reason", reason)
        .put("eligible", eligible).put("blockers", org.json.JSONArray(blockers))
        .put("bundle_digest", bundleDigest).put("asset_sha256", JSONObject(assetSha256))
        .put("generation", generation).put("expires_at_ms", expiresAtMs ?: JSONObject.NULL)
        .put("request_id", requestId ?: JSONObject.NULL).put("physical_stop_confirmed", false)
        .put("command", command?.let { JSONObject().put("forward_m_s", it.forward)
            .put("lateral_m_s", it.lateral).put("yaw_rate_rad_s", it.yawRate).put("duration_ms", it.durationMs)
            .put("height_offset_m", 0).put("roll_rad", 0).put("pitch_rad", 0) } ?: JSONObject.NULL)
}

/** One explicit operator enable, one provider request, one bounded command. No controller,
 * arm API, USB, network, shadow grant, renewal, or timer. The service independently polls.
 * Locks cover only validation and dispatch claims, never inference or blocking USB I/O. */
class PhysicalIntentAuthority(val binding: PhysicalPolicyBinding, private val clockNs: () -> Long) {
    private var state = if (binding.eligible) "disabled" else "blocked"
    private var reason = if (binding.eligible) "explicit_operator_enable_required" else "bundle_qualification_required"
    private var generation = 0L
    private var operation: Any? = null
    private var expiresAtNs: Long? = null
    private var request: PhysicalRequestHandle? = null
    private var command: ShadowIntent.Velocity? = null
    private var lease: Any? = null
    private var pendingFrame: PhysicalFrameTicket? = null
    private var frameClaimed = false
    private var manualBlocked = false
    private var lastSample = -1L
    private var lastObservedNs = -1L
    private var lastNowNs = -1L
    private var stopPending: String? = null
    private var closed = false

    private fun now(): Long {
        val next = try { clockNs() } catch (_: Exception) { -1L }
        if (next < 0 || next < lastNowNs) {
            revokeLocked("host_monotonic_clock_failed", "blocked")
            closed = true
            error("host_monotonic_clock_failed")
        }
        lastNowNs = next
        return next
    }
    private fun pollAt(at: Long) {
        if (expiresAtNs?.let { at >= it } == true && operation != null)
            revokeLocked("physical_authority_or_command_expired", "expired")
    }
    private fun revokeLocked(why: String, next: String = "cancelled") {
        if (operation != null) { stopPending = why; manualBlocked = true }
        generation++
        operation = null; expiresAtNs = null; lease = null; pendingFrame = null
        command = null; frameClaimed = false; state = next; reason = why
    }
    private fun view() = PhysicalIntentStatus(state, reason, binding.eligible, binding.blockers,
        binding.digest, binding.assetSha256, generation, expiresAtNs?.div(1_000_000), request?.requestId, command)

    @Synchronized fun status(): PhysicalIntentStatus {
        runCatching { pollAt(now()) }
        return view()
    }
    @Synchronized fun enable(allowPhysical: Boolean, operationIdentity: Any, bundleDigest: String, expectedGeneration: Long) {
        val at = now(); pollAt(at)
        check(expectedGeneration == generation) { "stale_operator_generation" }
        check(!closed && operation == null && stopPending == null && !manualBlocked) { "fresh_stopped_authority_required" }
        require(allowPhysical) { "explicit_physical_opt_in_required" }
        check(binding.eligible) { "bundle_qualification_required" }
        check(bundleDigest == binding.digest) { "loaded_bundle_identity_changed" }
        check(at <= Long.MAX_VALUE - AUTHORITY_NS)
        operation = operationIdentity; expiresAtNs = at + AUTHORITY_NS
        request = null; command = null; lease = null; pendingFrame = null
        lastSample = -1; lastObservedNs = -1; frameClaimed = false
        state = "enabled"; reason = "one_explicit_text_request_allowed"; generation++
    }
    @Synchronized fun beginRequest(text: String, expectedGeneration: Long): PhysicalRequestHandle {
        val at = now(); pollAt(at)
        check(expectedGeneration == generation) { "stale_operator_generation" }
        require(text.isNotBlank() && text.codePointCount(0, text.length) <= 2_000)
        check(!closed && state == "enabled" && operation != null && request == null) { "explicit_physical_enable_required" }
        val expiry = requireNotNull(expiresAtNs)
        check(expiry - at > 1_000_000L) { "physical_authority_expiring" }
        return PhysicalRequestHandle(UUID.randomUUID().toString(), expiry / 1_000_000,
            minOf(expiry, at + PROVIDER_NS) / 1_000_000).also {
            request = it; state = "pending"; reason = "awaiting_intent_only_response"
        }
    }
    @Synchronized fun submit(handle: PhysicalRequestHandle, raw: String, claimPublication: () -> Boolean = { true }): PhysicalIntentStatus {
        val at = now(); pollAt(at)
        if (request !== handle || state != "pending" || operation == null) return view()
        val admittedGeneration = generation
        val admittedOperation = operation
        if (at / 1_000_000 >= handle.providerDeadlineMs) {
            revokeLocked("provider_result_late_or_cancelled"); return view()
        }
        val intent = try { ShadowIntentDecoder.parse(raw) } catch (_: Exception) {
            revokeLocked("strict_intent_contract_failed"); return view()
        }
        if (intent !is ShadowIntent.Velocity) {
            revokeLocked(when (intent) {
                is ShadowIntent.Clarify -> "clarification_required"
                is ShadowIntent.Unsupported -> "unsupported_request"
                else -> "model_requested_stop"
            }); return view()
        }
        if (!binding.capabilities.accepts(intent) || intent.durationMs > binding.capabilities.maxDurationMs) {
            revokeLocked("outside_qualified_command_or_duration_bounds"); return view()
        }
        fun stillPending() = request === handle && state == "pending" && !closed &&
            operation === admittedOperation && generation == admittedGeneration
        val decodedAt = now(); pollAt(decodedAt)
        if (!stillPending()) return view()
        if (decodedAt / 1_000_000 >= handle.providerDeadlineMs) {
            revokeLocked("provider_result_late_or_cancelled"); return view()
        }
        val durationNs = intent.durationMs * 1_000_000L
        if (requireNotNull(expiresAtNs) - decodedAt < durationNs) {
            revokeLocked("command_outlives_original_authority_deadline"); return view()
        }
        // Decode and validate before claiming publication, which cancels the provider
        // timer. Recheck ownership and both deadlines after that external callback:
        // a successful claim is not permission to publish a late/replaced request.
        val claimed = claimPublication()
        val completedAt = now(); pollAt(completedAt)
        if (!stillPending()) return view()
        if (!claimed || completedAt / 1_000_000 >= handle.providerDeadlineMs) {
            revokeLocked("provider_result_late_or_cancelled"); return view()
        }
        if (requireNotNull(expiresAtNs) - completedAt < durationNs) {
            revokeLocked("command_outlives_original_authority_deadline"); return view()
        }
        expiresAtNs = completedAt + durationNs; command = intent; lease = Any()
        state = "leased"; reason = "bounded_command_waiting_for_controller_frame"
        return view()
    }
    @Synchronized fun fail(handle: PhysicalRequestHandle) {
        if (request === handle && state == "pending") revokeLocked("provider_failed")
    }
    @Synchronized fun beginFrame(operationIdentity: Any, bundleDigest: String,
                                 sampleSequence: Long, observedAtNs: Long, feedbackComplete: Boolean): PhysicalFrameTicket? {
        val at = now(); pollAt(at)
        if (state != "leased") return null
        if (operation !== operationIdentity || binding.digest != bundleDigest) {
            revokeLocked("physical_operation_or_bundle_changed"); error("physical_operation_or_bundle_changed")
        }
        check(pendingFrame == null) { "physical_frame_already_pending" }
        if (!feedbackComplete) {
            revokeLocked("physical_intent_requires_complete_feedback"); error("physical_intent_requires_complete_feedback")
        }
        if (sampleSequence <= lastSample || observedAtNs <= lastObservedNs || !fresh(observedAtNs, at)) {
            revokeLocked("physical_feedback_stale_future_or_replayed"); error("physical_feedback_stale_future_or_replayed")
        }
        lastSample = sampleSequence; lastObservedNs = observedAtNs; frameClaimed = true
        val intent = requireNotNull(command)
        return PhysicalFrameTicket(requireNotNull(lease), generation, operationIdentity, bundleDigest,
            MotionRequest(forward = intent.forward.toFloat(), lateral = intent.lateral.toFloat(),
                yawRate = intent.yawRate.toFloat(), heightOffset = 0f, roll = 0f, pitch = 0f),
            sampleSequence, observedAtNs).also { pendingFrame = it }
    }
    /** Called by the writer immediately before its atomic in-flight dispatch claim. */
    @Synchronized fun withFrameWrite(ticket: PhysicalFrameTicket, claim: () -> Unit) {
        val at = now(); pollAt(at)
        if (!(ticket === pendingFrame && ticket.lease === lease && ticket.generation == generation &&
            ticket.operation === operation && ticket.bundleDigest == binding.digest && state == "leased" &&
            fresh(ticket.observedAtNs, at))) {
            revokeLocked("physical_frame_authority_expired_or_revoked")
            error("physical_frame_authority_expired_or_revoked")
        }
        pendingFrame = null // One use even if the transport's claim subsequently fails.
        try { claim() } catch (error: Exception) { revokeLocked("physical_dispatch_failed"); throw error }
    }
    /** Manual frames computed before the first leased frame cannot outlive takeover. */
    @Synchronized fun withManualFrameWrite(claim: () -> Unit) {
        pollAt(now())
        check(!manualBlocked && !frameClaimed && stopPending == null) { "manual_frame_fenced_by_physical_source" }
        claim()
    }
    @Synchronized fun failFrame(ticket: PhysicalFrameTicket) {
        if (pendingFrame === ticket) revokeLocked("physical_frame_computation_failed")
    }
    @Synchronized fun resetForExplicitManualOperation() {
        check(operation == null) { "physical_authority_must_be_revoked_first" }
        manualBlocked = false; frameClaimed = false; stopPending = null
    }
    @Synchronized fun cancel(expectedGeneration: Long) {
        check(expectedGeneration >= generation && expectedGeneration - generation <= 1) { "stale_operator_generation" }
        generation = expectedGeneration
        revokeLocked("operator_cancelled")
    }
    @Synchronized fun revoke(why: String) { if (!closed) revokeLocked(why) }
    @Synchronized fun terminalRequestToCancel(): PhysicalRequestHandle? {
        runCatching { pollAt(now()) }
        return if (state !in listOf("enabled", "pending", "leased")) request else null
    }
    @Synchronized fun takeStopRequest(): String? = stopPending.also { stopPending = null }
    @Synchronized fun close() { revokeLocked("physical_service_closed", "closed"); closed = true }
    private fun fresh(sampleNs: Long, at: Long) = sampleNs >= 0 && at >= sampleNs && at - sampleNs <= FEEDBACK_MAX_AGE_NS
    companion object {
        const val AUTHORITY_NS = 10_000_000_000L
        const val PROVIDER_NS = 8_000_000_000L
        const val FEEDBACK_MAX_AGE_NS = 40_000_000L
    }
}

/** Only shared robot/operator lifecycle events revoke both modes. Shadow panel
 * Cancel/Stop and preview results retain their compute-only semantics. */
internal fun sharedPhysicalRevocation(reason: ShadowCancelReason): String? = when (reason) {
    ShadowCancelReason.OPERATOR_CANCEL -> null
    ShadowCancelReason.MANUAL_CONTROL, ShadowCancelReason.OPERATOR_BACKGROUNDED,
    ShadowCancelReason.USB_DISCONNECTED, ShadowCancelReason.USB_RECONNECT,
    ShadowCancelReason.PHYSICAL_STOP, ShadowCancelReason.SERVICE_DESTROYED -> reason.name.lowercase()
}
