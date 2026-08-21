package com.leo.pixelrobot.policy

import android.content.res.AssetManager
import android.os.SystemClock
import com.leo.pixelrobot.robot.RobotProtocol
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.channels.BufferOverflow
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import java.io.Closeable
import java.util.concurrent.atomic.AtomicReference

data class MotionRequest(val forward: Float = 0f, val yawRate: Float = 0f)

data class PolicyRuntimeStatus(
    val active: Boolean = false,
    val commanding: Boolean = false,
    val holdingPose: Boolean = false,
    val detail: String = "Policy idle",
    val sequence: Long = 0,
    val inferenceMilliseconds: Double? = null,
    val executionProvider: String? = null,
    val torquePercent: Int? = null,
    val gyroBiasDps: FloatArray? = null,
    val trackingErrorDegrees: Float? = null,
    val peakTrackingErrorDegrees: Float? = null,
    val worstTrackingServoId: Int? = null,
)

class PolicyController(
    assets: AssetManager,
    private val scope: CoroutineScope,
    calibrationOverrideJson: String? = null,
    private val saveCalibrationOverride: (String) -> Unit = {},
    private val send: (ByteArray) -> Unit,
) : Closeable {
    private val contract = PolicyContract.load(assets)
    private var calibration = RobotCalibration.load(assets, contract.profileId, calibrationOverrideJson)
    private val policy = OnnxPolicy(
        assets,
        contract.profileId,
        contract.profileSha256,
        contract.weightsSha256,
    )
    private val messages = Channel<JSONObject>(capacity = 64, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    private val request = AtomicReference(MotionRequest())
    private val mutableStatus = MutableStateFlow(
        PolicyRuntimeStatus(detail = "Actor ready", executionProvider = policy.executionProvider),
    )
    val status: StateFlow<PolicyRuntimeStatus> = mutableStatus.asStateFlow()
    private var runJob: Job? = null

    fun updateRequest(forward: Float, yawRate: Float) {
        contract.requireRequest(forward, 0f, yawRate)
        request.set(MotionRequest(forward, yawRate))
    }

    fun onRobotMessage(message: JSONObject) {
        if (runJob?.isActive == true) messages.trySend(message)
    }

    fun onLinkReady() {
        if (!mutableStatus.value.active) {
            mutableStatus.value = PolicyRuntimeStatus(
                detail = "Actor ready",
                sequence = mutableStatus.value.sequence,
                inferenceMilliseconds = mutableStatus.value.inferenceMilliseconds,
                executionProvider = policy.executionProvider,
            )
        }
    }

    fun startSuspendedTest() {
        check(runJob?.isActive != true) { "policy is already running" }
        while (messages.tryReceive().isSuccess) Unit
        runJob = scope.launch { run() }
    }

    fun captureCurrentStartPose() {
        check(runJob?.isActive != true) { "another robot operation is already running" }
        while (messages.tryReceive().isSuccess) Unit
        runJob = scope.launch { captureStartPose() }
    }

    fun standAtCapturedPose() {
        check(runJob?.isActive != true) { "another robot operation is already running" }
        while (messages.tryReceive().isSuccess) Unit
        runJob = scope.launch { moveToCapturedPose() }
    }

    fun stop(reason: String = "Stop requested") {
        request.set(MotionRequest())
        runJob?.cancel(CancellationException(reason))
        safeDisarm(reason)
    }

    private suspend fun run() {
        val startedFromHeldPose = mutableStatus.value.holdingPose
        var armAttempted = false
        val gravity = GravityEstimator()
        var previousJointPosition: FloatArray? = null
        var previousSampleMs: Long? = null
        var history: Array<FloatArray>? = null
        var command = floatArrayOf(request.get().forward, 0f, request.get().yawRate)
        var filteredAction = FloatArray(ACTION_COUNT)
        var appliedAction = FloatArray(ACTION_COUNT)
        val actionsBySequence = mutableMapOf(0L to appliedAction.copyOf())
        var sequence = 0L
        var peakTrackingError = 0f

        mutableStatus.value = PolicyRuntimeStatus(
            active = true,
            holdingPose = startedFromHeldPose,
            detail = if (startedFromHeldPose) {
                "Checking the held start pose without dropping torque"
            } else {
                "Checking the captured start pose with torque off"
            },
            executionProvider = policy.executionProvider,
        )
        try {
            val preflightAngles = if (startedFromHeldPose) {
                readAnglesWhileHolding(sampleCount = 2)
            } else {
                readAnglesWithVerifiedTorqueOff(sampleCount = 2)
            }
            val referenceError = calibration.maximumReferenceErrorDegrees(preflightAngles)
            require(referenceError <= START_POSE_TOLERANCE_DEG) {
                "robot is ${"%.1f".format(referenceError)} degrees from its captured start pose"
            }
            val sessionGyroBias = measureStationaryGyroBiasDps()
            mutableStatus.value = mutableStatus.value.copy(
                detail = "Setting policy torque to 100%",
                torquePercent = POLICY_TORQUE_LIMIT / 10,
                gyroBiasDps = sessionGyroBias.copyOf(),
            )
            send(RobotProtocol.torqueLimitAll(POLICY_TORQUE_LIMIT))
            awaitMessage("ok", 2_000) { it.optString("cmd") == "servo_torque_limit" }
            mutableStatus.value = mutableStatus.value.copy(detail = "Arming guarded firmware transport")
            armAttempted = true
            send(RobotProtocol.arm())
            var state = awaitPolicyState(expectedSequence = 0, timeoutMs = 2_000)
            var nextTickNs = SystemClock.elapsedRealtimeNanos()
            while (currentCoroutineContext().isActive) {
                val sampleMs = state.getLong("sample_ms") and 0xffff_ffffL
                val dt = previousSampleMs?.let { previous ->
                    val elapsed = (sampleMs - previous) and 0xffff_ffffL
                    if (elapsed in 5..100) elapsed / 1000f else FRAME_DT
                } ?: FRAME_DT
                val vectors = stateVectors(state, gravity, dt, sessionGyroBias)
                val joints = vectors.second
                val jointVelocity = previousJointPosition?.let { previous ->
                    FloatArray(ACTION_COUNT) { (joints[it] - previous[it]) / dt }
                } ?: FloatArray(ACTION_COUNT)

                val target = request.get()
                val commandTarget = floatArrayOf(target.forward, 0f, target.yawRate)
                val alpha = (FRAME_DT / contract.commandSmoothingSeconds).coerceAtMost(1f)
                command = FloatArray(3) { command[it] + alpha * (commandTarget[it] - command[it]) }
                val stateSequence = state.getLong("seq")
                val frame = vectors.first + command + joints +
                    FloatArray(ACTION_COUNT) { 0.05f * jointVelocity[it] } +
                    requireNotNull(actionsBySequence[stateSequence]) { "missing action for feedback sequence $stateSequence" }
                if (history == null) history = Array(HISTORY_COUNT) { frame.copyOf() }
                else {
                    for (index in 0 until HISTORY_COUNT - 1) history[index] = history[index + 1]
                    history[HISTORY_COUNT - 1] = frame
                }
                val observation = history.flatMap { it.asIterable() }.toFloatArray()
                val inferenceStarted = SystemClock.elapsedRealtimeNanos()
                val requestedAction = policy.action(observation)
                val inferenceMs = (SystemClock.elapsedRealtimeNanos() - inferenceStarted) / 1_000_000.0
                val actionStep = contract.applyAction(
                    requestedAction,
                    filteredAction,
                    appliedAction,
                    command,
                )
                filteredAction = actionStep.filtered
                appliedAction = actionStep.applied
                val policyPosition = FloatArray(ACTION_COUNT) {
                    contract.positionTargetScaleRadians * appliedAction[it]
                }
                val targets = calibration.servoTargets(policyPosition)

                sequence += 1
                actionsBySequence[sequence] = appliedAction
                send(RobotProtocol.policyFrame(sequence, targets))
                previousJointPosition = joints
                previousSampleMs = sampleMs
                actionsBySequence.keys.removeAll { it < sequence - 2 }
                state = awaitPolicyState(sequence, FEEDBACK_TIMEOUT_MS)
                val tracking = maximumTrackingError(state, targets)
                peakTrackingError = maxOf(peakTrackingError, tracking.second)
                mutableStatus.value = PolicyRuntimeStatus(
                    active = true,
                    commanding = true,
                    detail = "Continuous policy test running at 100% torque",
                    sequence = sequence,
                    inferenceMilliseconds = inferenceMs,
                    executionProvider = policy.executionProvider,
                    torquePercent = POLICY_TORQUE_LIMIT / 10,
                    gyroBiasDps = sessionGyroBias.copyOf(),
                    trackingErrorDegrees = tracking.second,
                    peakTrackingErrorDegrees = peakTrackingError,
                    worstTrackingServoId = tracking.first,
                )
                nextTickNs += FRAME_PERIOD_NS
                val remainingNs = nextTickNs - SystemClock.elapsedRealtimeNanos()
                if (remainingNs > 0) delay((remainingNs + 999_999L) / 1_000_000L)
            }
            safeDisarm("Policy test ended")
        } catch (timeout: TimeoutCancellationException) {
            if (startedFromHeldPose && !armAttempted) {
                keepHoldingAfterFailedStart("Policy not started: feedback timeout; stand torque remains on")
            } else {
                safeDisarm("Policy stopped: feedback timeout")
            }
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: Throwable) {
            if (startedFromHeldPose && !armAttempted) {
                keepHoldingAfterFailedStart(
                    "Policy not started: ${error.message}; stand torque remains on",
                )
            } else {
                safeDisarm("Policy stopped: ${error.message}")
            }
        } finally {
            runJob = null
        }
    }

    private suspend fun captureStartPose() {
        mutableStatus.value = PolicyRuntimeStatus(
            active = true,
            detail = "Disabling and verifying torque before reading the stand pose",
            executionProvider = policy.executionProvider,
        )
        try {
            val angles = readAnglesWithVerifiedTorqueOff(sampleCount = CAPTURE_SAMPLE_COUNT)
            val captured = calibration.captureReference(angles)
            val firmwareConfig = requestFirmwareConfig()
            val previousFirmwareServos = calibration.firmwareServoConfiguration(firmwareConfig)
            val capturedFirmwareServos = captured.calibration.firmwareServoConfiguration(firmwareConfig)
            try {
                applyFirmwareCalibration(captured.calibration, capturedFirmwareServos)
                saveCalibrationOverride(captured.json)
            } catch (failure: Throwable) {
                val rollbackFailure = runCatching {
                    applyFirmwareCalibration(calibration, previousFirmwareServos)
                }.exceptionOrNull()
                if (rollbackFailure != null) {
                    error(
                        "${failure.message}; restoring the previous ESP32 calibration also failed: " +
                            rollbackFailure.message,
                    )
                }
                throw failure
            }
            calibration = captured.calibration
            mutableStatus.value = PolicyRuntimeStatus(
                detail = (
                    "Captured all 12 policy-zero positions and synchronized the ESP32 with torque verified off; " +
                        "largest reference change %.1f degrees"
                ).format(captured.largestShiftDegrees),
                executionProvider = policy.executionProvider,
            )
        } catch (error: Throwable) {
            runCatching { send(RobotProtocol.torqueAll(false)) }
            mutableStatus.value = PolicyRuntimeStatus(
                detail = "Start-pose capture failed: ${error.message}",
                executionProvider = policy.executionProvider,
            )
        } finally {
            runJob = null
        }
    }

    private suspend fun requestFirmwareConfig(): JSONObject {
        send(RobotProtocol.configGet())
        return awaitMessage("config", 3_000)
    }

    private suspend fun applyFirmwareCalibration(
        expected: RobotCalibration,
        servos: JSONArray,
    ) {
        send(RobotProtocol.configSet(servos))
        awaitMessage("ok", 3_000) { it.optString("cmd") == "config_set" }
        val verified = awaitMessage("config", 3_000)
        expected.requireFirmwareConfigurationMatches(verified)
    }

    private suspend fun moveToCapturedPose() {
        mutableStatus.value = PolicyRuntimeStatus(
            active = true,
            detail = "Checking the resting pose with torque off",
            executionProvider = policy.executionProvider,
        )
        try {
            val measured = readAnglesWithVerifiedTorqueOff(sampleCount = 2)
            val target = calibration.servoTargets(FloatArray(ACTION_COUNT))
            val maximumDelta = calibration.servoIds.maxOf { servoId ->
                kotlin.math.abs(requireNotNull(target[servoId]) - requireNotNull(measured[servoId]))
            }
            require(maximumDelta <= MAX_STAND_DELTA_DEG) {
                "resting pose is ${"%.1f".format(maximumDelta)} degrees from captured zero; position it manually first"
            }
            val stepCount = maxOf(1, kotlin.math.ceil(maximumDelta / STAND_STEP_DEG).toInt())
            val poses = (1..stepCount).map { stepIndex ->
                val fraction = stepIndex.toFloat() / stepCount
                calibration.servoIds.indices.associate { index ->
                    val servoId = calibration.servoIds[index]
                    val start = requireNotNull(measured[servoId])
                    val interpolated = start + (requireNotNull(target[servoId]) - start) * fraction
                    servoId to interpolated.coerceIn(
                        calibration.minimums[index],
                        calibration.maximums[index],
                    )
                }
            }

            send(RobotProtocol.torqueLimitAll(STAND_TORQUE_LIMIT))
            awaitMessage("ok", 2_000) { it.optString("cmd") == "servo_torque_limit" }
            send(RobotProtocol.torqueAll(true))
            awaitMessage("ok", 2_000) { it.optString("cmd") == "servo_torque" }
            val poseBatches = poses.chunked(STAND_BATCH_SIZE)
            poseBatches.forEachIndexed { batchIndex, batch ->
                val command = RobotProtocol.playPoseSequence(
                    batch,
                    stepMilliseconds = STAND_STEP_MS,
                    speed = STAND_SPEED,
                    acceleration = STAND_ACCELERATION,
                )
                send(command)
                try {
                    awaitMessage("ok", 3_000) { it.optString("cmd") == "play" }
                } catch (failure: Throwable) {
                    error(
                        "stand batch ${batchIndex + 1} of ${poseBatches.size} failed at " +
                            "${command.size} JSON bytes: ${failure.message}",
                    )
                }
                mutableStatus.value = mutableStatus.value.copy(
                    detail = "Moving to captured zero, batch ${batchIndex + 1} of ${poseBatches.size}",
                )
                delay(batch.size * STAND_STEP_MS.toLong() + STAND_BATCH_GAP_MS)
            }
            delay(STAND_SETTLE_MS)

            send(RobotProtocol.readAll())
            val state = awaitMessage("state", 3_000)
            val finalAngles = state.getJSONObject("measured")
            val statusErrors = state.optJSONObject("statusErrors")
            var worstServo = -1
            var worstError = 0f
            calibration.servoIds.forEach { servoId ->
                require(statusErrors?.optInt(servoId.toString(), 0) == 0) {
                    "servo $servoId reported a hardware error while standing"
                }
                val error = kotlin.math.abs(
                    finalAngles.getDouble(servoId.toString()).toFloat() - requireNotNull(target[servoId]),
                )
                if (error > worstError) {
                    worstError = error
                    worstServo = servoId
                }
            }
            require(worstError <= STAND_FINAL_TOLERANCE_DEG) {
                "captured zero was not reached; servo $worstServo is ${"%.1f".format(worstError)} degrees away"
            }
            mutableStatus.value = PolicyRuntimeStatus(
                holdingPose = true,
                detail = "Standing at captured zero; ready for the continuous test",
                executionProvider = policy.executionProvider,
            )
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: Throwable) {
            safeDisarm("Stand failed: ${error.message}")
        } finally {
            runJob = null
        }
    }

    private suspend fun readAnglesWithVerifiedTorqueOff(sampleCount: Int): Map<Int, Float> {
        require(sampleCount >= 1)
        send(RobotProtocol.torqueAll(false))
        awaitMessage("ok", 2_000) { it.optString("cmd") == "servo_torque" }
        for (servoId in 1..ACTION_COUNT) {
            send(RobotProtocol.servoBusProbe(servoId, TORQUE_ENABLE_ADDRESS, 1))
            val response = awaitMessage("servo_bus_probe", 2_000) {
                it.optInt("id", -1) == servoId
            }
            val raw = response.optJSONArray("bytes")
                ?: error("servo $servoId torque register was not returned")
            require(raw.length() >= 7 && raw.getInt(2) == servoId && raw.getInt(4) == 0 && raw.getInt(5) == 0) {
                "servo $servoId torque-off verification failed"
            }
        }

        return readStableAngles(sampleCount)
    }

    private suspend fun readAnglesWhileHolding(sampleCount: Int): Map<Int, Float> =
        readStableAngles(sampleCount)

    private suspend fun readStableAngles(sampleCount: Int): Map<Int, Float> {
        require(sampleCount >= 1)
        val samples = ArrayList<Map<Int, Float>>(sampleCount)
        repeat(sampleCount) { sampleIndex ->
            send(RobotProtocol.readAll())
            val state = awaitMessage("state", 3_000)
            val measured = state.optJSONObject("measured")
                ?: error("servo position response is missing")
            samples += (1..ACTION_COUNT).associateWith { servoId ->
                measured.getDouble(servoId.toString()).toFloat().also {
                    require(it.isFinite() && it in 0f..360f) {
                        "servo $servoId returned an invalid position"
                    }
                }
            }
            if (sampleIndex + 1 < sampleCount) delay(CAPTURE_SAMPLE_INTERVAL_MS)
        }

        return (1..ACTION_COUNT).associateWith { servoId ->
            val values = samples.map { requireNotNull(it[servoId]) }
            require(values.max() - values.min() <= CAPTURE_STABILITY_DEG) {
                "servo $servoId moved during the position check"
            }
            values.average().toFloat()
        }
    }

    private suspend fun awaitMessage(
        type: String,
        timeoutMs: Long,
        accept: (JSONObject) -> Boolean = { true },
    ): JSONObject = withTimeout(timeoutMs) {
        while (true) {
            val message = messages.receive()
            if (message.optString("type") == "error") {
                error(message.optString("message", "ESP32 error"))
            }
            if (message.optString("type") == type && accept(message)) return@withTimeout message
        }
        error("unreachable")
    }

    private suspend fun awaitPolicyState(expectedSequence: Long, timeoutMs: Long): JSONObject = withTimeout(timeoutMs) {
        while (true) {
            val message = messages.receive()
            when (message.optString("type")) {
                "error" -> error(message.optString("message", "ESP32 error"))
                "policy_disarmed" -> error(message.optString("reason", "firmware disarmed"))
                "policy_state" -> {
                    val sequence = message.optLong("seq", -1)
                    if (sequence < expectedSequence) continue
                    require(sequence == expectedSequence) { "feedback sequence $sequence is ahead of $expectedSequence" }
                    require(message.optBoolean("armed", false)) { "firmware reports policy disarmed" }
                    require(message.optBoolean("feedback_complete", false)) { "servo feedback is incomplete" }
                    return@withTimeout message
                }
            }
        }
        error("unreachable")
    }

    private suspend fun measureStationaryGyroBiasDps(): FloatArray {
        mutableStatus.value = mutableStatus.value.copy(
            detail = "Sampling the ESP32 gyro",
        )
        val gyroSamples = ArrayList<FloatArray>(GYRO_ZERO_SAMPLE_COUNT)
        repeat(GYRO_ZERO_SAMPLE_COUNT) { sampleIndex ->
            send(RobotProtocol.imuStatus())
            val sample = awaitMessage("imu", 2_000)
            require(sample.optBoolean("available", false)) { "ESP32 IMU is unavailable" }
            gyroSamples += sample.getJSONObject("gyro").vector3()
            if (sampleIndex + 1 < GYRO_ZERO_SAMPLE_COUNT) delay(GYRO_ZERO_SAMPLE_INTERVAL_MS)
        }
        return FloatArray(3) { axis -> gyroSamples.map { it[axis] }.average().toFloat() }
    }

    private fun maximumTrackingError(
        state: JSONObject,
        targetsByServoId: Map<Int, Float>,
    ): Pair<Int, Float> {
        val ids = state.getJSONArray("ids").intArray()
        val angles = state.getJSONArray("angles_deg").floatArray()
        require(ids.size == ACTION_COUNT && angles.size == ACTION_COUNT)
        var worstServoId = ids.first()
        var worstError = -1f
        ids.indices.forEach { index ->
            val servoId = ids[index]
            val error = kotlin.math.abs(angles[index] - requireNotNull(targetsByServoId[servoId]))
            if (error > worstError) {
                worstError = error
                worstServoId = servoId
            }
        }
        return worstServoId to worstError
    }

    private fun stateVectors(
        state: JSONObject,
        gravity: GravityEstimator,
        dt: Float,
        gyroBiasDps: FloatArray,
    ): Pair<FloatArray, FloatArray> {
        val ids = state.getJSONArray("ids").intArray()
        val angles = state.getJSONArray("angles_deg").floatArray()
        require(ids.size == ACTION_COUNT && angles.size == ACTION_COUNT)
        val joints = calibration.policyPositions(ids.indices.associate { ids[it] to angles[it] })
        val gyroSensor = state.getJSONArray("gyro_dps").floatArray()
        val accelerationSensor = state.getJSONArray("accel_mg").floatArray()
        val gyroBody = calibration.bodyGyroRadPerSecond(gyroSensor, gyroBiasDps)
        val accelerationBody = calibration.bodyAccelerationMg(accelerationSensor)
        val projectedGravity = gravity.update(accelerationBody, gyroBody, calibration.gravitySign, dt)
        return (gyroBody + projectedGravity) to joints
    }

    private fun safeDisarm(detail: String) {
        runCatching { send(RobotProtocol.command("stop")) }
        runCatching { send(RobotProtocol.command("policy_disarm")) }
        runCatching { send(RobotProtocol.torqueAll(false)) }
        val previous = mutableStatus.value
        mutableStatus.value = PolicyRuntimeStatus(
            active = false,
            holdingPose = false,
            detail = detail,
            sequence = previous.sequence,
            inferenceMilliseconds = previous.inferenceMilliseconds,
            executionProvider = policy.executionProvider,
            torquePercent = previous.torquePercent,
            gyroBiasDps = previous.gyroBiasDps?.copyOf(),
            trackingErrorDegrees = previous.trackingErrorDegrees,
            peakTrackingErrorDegrees = previous.peakTrackingErrorDegrees,
            worstTrackingServoId = previous.worstTrackingServoId,
        )
    }

    private fun keepHoldingAfterFailedStart(detail: String) {
        request.set(MotionRequest())
        val previous = mutableStatus.value
        mutableStatus.value = PolicyRuntimeStatus(
            active = false,
            holdingPose = true,
            detail = detail,
            sequence = previous.sequence,
            inferenceMilliseconds = previous.inferenceMilliseconds,
            executionProvider = policy.executionProvider,
            torquePercent = STAND_TORQUE_LIMIT / 10,
        )
    }

    override fun close() {
        stop("Runtime closed")
        policy.close()
    }

    companion object {
        private const val ACTION_COUNT = 12
        private const val HISTORY_COUNT = 4
        private const val FRAME_DT = 0.02f
        private const val FRAME_PERIOD_NS = 20_000_000L
        private const val FEEDBACK_TIMEOUT_MS = 80L
        private const val POLICY_TORQUE_LIMIT = 1000
        private const val TORQUE_ENABLE_ADDRESS = 0x28
        private const val CAPTURE_SAMPLE_COUNT = 5
        private const val CAPTURE_SAMPLE_INTERVAL_MS = 40L
        private const val CAPTURE_STABILITY_DEG = 1f
        private const val START_POSE_TOLERANCE_DEG = 12f
        private const val MAX_STAND_DELTA_DEG = 90f
        private const val STAND_STEP_DEG = 6f
        private const val STAND_STEP_MS = 200
        private const val STAND_BATCH_SIZE = 1
        private const val STAND_BATCH_GAP_MS = 100L
        private const val STAND_SETTLE_MS = 2_000L
        private const val STAND_FINAL_TOLERANCE_DEG = 12f
        private const val STAND_TORQUE_LIMIT = 1000
        private const val STAND_SPEED = 360
        private const val STAND_ACCELERATION = 60
        private const val GYRO_ZERO_SAMPLE_COUNT = 25
        private const val GYRO_ZERO_SAMPLE_INTERVAL_MS = 20L
    }
}

private fun JSONArray.floatArray(): FloatArray = FloatArray(length()) { index ->
    getDouble(index).toFloat().also { require(it.isFinite()) }
}

private fun JSONArray.intArray(): IntArray = IntArray(length()) { index -> getInt(index) }

private fun JSONObject.vector3(): FloatArray = floatArrayOf(
    getDouble("x").toFloat(),
    getDouble("y").toFloat(),
    getDouble("z").toFloat(),
).also { require(it.all(Float::isFinite)) }
