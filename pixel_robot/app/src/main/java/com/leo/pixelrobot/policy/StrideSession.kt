package com.leo.pixelrobot.policy

/** Offline-verified V21 clock and action processing. No transport or motor access. */
class StrideSession(private val contract: PolicyContract, referenceBytes: ByteArray) {
    private val reference = StrideReference.parse(
        referenceBytes, requireNotNull(contract.strideReferenceSha256), contract.profileSha256,
    )
    var elapsedSeconds = 0f
        private set
    private var frames = 0L

    init {
        require(contract.usesStrideReference)
        require(reference.positionScale == contract.positionTargetScaleRadians)
    }

    fun reset() { elapsedSeconds = 0f; frames = 0L }

    fun clock(): FloatArray = reference.clock(elapsedSeconds)

    fun action(
        residual: FloatArray,
        command: FloatArray,
        posture: FloatArray,
        gravity: FloatArray,
        previousFiltered: FloatArray,
        previousApplied: FloatArray,
    ): AppliedActionStep {
        val combined = reference.combined(residual, command, posture, gravity, elapsedSeconds)
        val step = contract.applyAction(combined, previousFiltered, previousApplied, command)
        // The simulator clears both histories after filtering during reset hold.
        return if (frames < 50) AppliedActionStep(FloatArray(12), FloatArray(12)) else step
    }

    /** Once per completed fresh-feedback frame; never for a retry or catch-up inference. */
    fun advance() {
        elapsedSeconds += contract.controlFrameSeconds
        frames += 1
        require(elapsedSeconds.isFinite())
    }
}
