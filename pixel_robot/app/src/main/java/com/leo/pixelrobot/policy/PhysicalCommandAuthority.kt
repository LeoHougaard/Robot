package com.leo.pixelrobot.policy

/** Independent operator authority. No ShadowGrant, provider handle or diagnostic is accepted. */
class PhysicalCommandAuthority(private val clockNs: () -> Long) {
    class Lease internal constructor(
        internal val generation: Long, internal val loadedIdentity: String,
        internal val deadlineNs: Long, val motion: MotionRequest,
    )
    private var generation = 0L
    private var current: Lease? = null
    private var lastClockNs = 0L

    @Synchronized fun authorizeOperatorTrial(
        operatorApproved: Boolean, physicalReadinessVerified: Boolean,
        loadedIdentity: String, motion: MotionRequest, durationMs: Int,
    ): Lease {
        check(operatorApproved && physicalReadinessVerified) { "Physical operator approval and readiness required" }
        require(loadedIdentity.matches(Regex("[a-f0-9]{64}")))
        require(durationMs in 1..5_000)
        require(listOf(motion.forward, motion.lateral, motion.yawRate).all { it.isFinite() })
        require(motion.heightOffset == 0f && motion.roll == 0f && motion.pitch == 0f)
        val deadline = Math.addExact(now(), durationMs * 1_000_000L)
        return Lease(++generation, loadedIdentity, deadline, motion.copy()).also { current = it }
    }

    @Synchronized fun revoke() { generation++; current = null }
    @Synchronized fun expired(): Boolean = current?.let { now() >= it.deadlineNs } ?: false

    /** Holds the authority lock through the actual bounded driver write, atomically with revocation. */
    @Synchronized fun write(
        lease: Lease, loadedIdentity: String, feedbackReceivedNs: Long?, write: () -> Unit,
    ) {
        val at = now()
        check(current === lease && lease.generation == generation) { "Physical lease revoked" }
        check(loadedIdentity == lease.loadedIdentity && at < lease.deadlineNs) { "Physical lease expired or assets changed" }
        if (feedbackReceivedNs != null)
            check(isFreshFeedbackTimestamp(feedbackReceivedNs, at)) { "Physical feedback is stale or future" }
        write()
    }

    private fun now(): Long {
        val at = clockNs()
        if (at < 0 || at < lastClockNs) { revoke(); error("Physical monotonic clock failed") }
        lastClockNs = at
        return at
    }
}
