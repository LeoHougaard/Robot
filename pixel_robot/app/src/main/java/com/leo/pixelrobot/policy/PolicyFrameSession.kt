package com.leo.pixelrobot.policy

/** Shared control math for the phone and sensor-replay verification. No motor access. */
class PolicyFrameSession(private val contract: PolicyContract, referenceBytes: ByteArray? = null) {
    private val builder = PolicyObservationBuilder(contract)
    private val stride = if (contract.usesStrideReference) {
        StrideSession(contract, requireNotNull(referenceBytes))
    } else {
        require(referenceBytes == null)
        null
    }
    private var history: Array<FloatArray>? = null
    private var filtered = FloatArray(12)
    private var applied = FloatArray(12)
    private var phase = 0
    val elapsedSeconds: Float get() = stride?.elapsedSeconds ?: 0f

    fun reset() {
        builder.reset()
        stride?.reset()
        history = null
        filtered.fill(0f)
        applied.fill(0f)
        phase = 0
    }

    fun observation(
        sensor: PolicySensorFrame,
        command: FloatArray,
        posture: FloatArray,
        acknowledgedAction: FloatArray,
    ): FloatArray {
        check(phase == 0) { "previous control frame has not completed" }
        val base = sensor.imu + command + sensor.position +
            FloatArray(12) { .05f * sensor.velocity[it] } + acknowledgedAction
        val frame = builder.frame(base, sensor.current,
            sensor.dt * 1000f / contract.timingReferenceMilliseconds)
        val frames = history?.also {
            for (index in 0 until it.lastIndex) it[index] = it[index + 1]
            it[it.lastIndex] = frame
        } ?: Array(contract.observationHistory) { frame.copyOf() }
        history = frames
        return builder.observation(frames, posture, stride?.clock() ?: FloatArray(0)).also { phase = 1 }
    }

    fun action(residual: FloatArray, command: FloatArray, posture: FloatArray, gravity: FloatArray): AppliedActionStep {
        check(phase == 1) { "action requires one fresh observation" }
        val next = stride?.action(residual, command, posture, gravity, filtered, applied)
            ?: contract.applyAction(residual, filtered, applied, command)
        filtered = next.filtered.copyOf()
        applied = next.applied.copyOf()
        phase = 2
        return next
    }

    /** Called only after the transport accepts a fresh feedback frame. */
    fun completeFrame() {
        check(phase == 2) { "feedback completion requires one computed action" }
        stride?.advance()
        phase = 0
    }
}
