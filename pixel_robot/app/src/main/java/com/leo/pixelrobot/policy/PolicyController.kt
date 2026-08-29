package com.leo.pixelrobot.policy

import android.content.res.AssetManager
import android.os.SystemClock
import com.leo.pixelrobot.robot.RobotProtocol
import com.leo.pixelrobot.robot.RunSessionRecorder
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
    val feedbackHertz: Double? = null,
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
    private val recorder: RunSessionRecorder,
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
        runCatching { recorder.recordEvent(
            "motion_request",
            JSONObject().put("forward_m_s", forward).put("yaw_rate_rad_s", yawRate),
        ) }
    }

    fun onRobotMessage(message: JSONObject) {
        messages.trySend(message)
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

    fun startPolicy() {
        check(runJob?.isActive != true) { "policy is already running" }
        while (messages.tryReceive().isSuccess) Unit
        runCatching {
            val initialRequest = request.get()
            recorder.recordEvent(
                "policy_start_requested",
                JSONObject()
                    .put("forward_m_s", initialRequest.forward)
                    .put("yaw_rate_rad_s", initialRequest.yawRate),
            )
        }
        runJob = scope.launch { run() }
    }

    fun standAtCapturedPose() {
        check(runJob?.isActive != true) { "another robot operation is already running" }
        while (messages.tryReceive().isSuccess) Unit
        runCatching {
            recorder.recordEvent("stand_requested")
        }
        runJob = scope.launch {
            try {
                withTimeout(STAND_OPERATION_TIMEOUT_MS) { moveToCapturedPose() }
            } catch (timeout: TimeoutCancellationException) {
                safeDisarm("Stand failed: controller did not finish within 15 seconds")
            } finally {
                runJob = null
            }
        }
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
        val targetsBySequence = mutableMapOf<Long, Map<Int, Float>>()
        var sequence = 0L
        var peakTrackingError = 0f

        mutableStatus.value = PolicyRuntimeStatus(
            active = true,
            holdingPose = startedFromHeldPose,
            detail = if (startedFromHeldPose) {
                "Checking servo feedback without dropping torque"
            } else {
                "Checking servo feedback with torque off"
            },
            executionProvider = policy.executionProvider,
        )
        try {
            val preflightAngles = if (startedFromHeldPose) {
                readAnglesWhileHolding(sampleCount = 2)
            } else {
                readAnglesWithVerifiedTorqueOff(sampleCount = 2)
            }
            recorder.recordEvent(
                "policy_preflight_positions",
                JSONObject().put("servo_angles_deg", preflightAngles.jsonObject()),
            )
            val sessionGyroBias = measureStationaryGyroBiasDps()
            recorder.recordEvent(
                "session_gyro_bias",
                JSONObject().put("gyro_bias_sensor_dps", sessionGyroBias.jsonArray()),
            )
            mutableStatus.value = mutableStatus.value.copy(
                detail = "Setting policy torque to 100%",
                torquePercent = SERVO_TORQUE_LIMIT / 10,
                gyroBiasDps = sessionGyroBias.copyOf(),
            )
            send(RobotProtocol.torqueLimitAll(SERVO_TORQUE_LIMIT))
            awaitMessage("ok", 2_000) { it.optString("cmd") == "servo_torque_limit" }
            mutableStatus.value = mutableStatus.value.copy(detail = "Arming guarded firmware transport")
            armAttempted = true
            send(RobotProtocol.arm())
            var state = awaitPolicyStateAfterTick(previousTick = -1, timeoutMs = 2_000)
            require(state.getLong("seq") == 0L && state.getLong("tick") == 0L) {
                "initial policy feedback must start at sequence 0, tick 0"
            }
            require(state.optBoolean("feedback_complete", false)) { "initial servo feedback is incomplete" }
            targetsBySequence[0L] = state.servoAnglesById()
            while (currentCoroutineContext().isActive) {
                val frameStartedNs = SystemClock.elapsedRealtimeNanos()
                val inputState = state
                val inputTick = state.getLong("tick")
                val sampleMs = state.getLong("sample_ms") and 0xffff_ffffL
                val dt = previousSampleMs?.let { previous ->
                    val elapsed = (sampleMs - previous) and 0xffff_ffffL
                    if (elapsed in 5..100) elapsed / 1000f else contract.controlFrameSeconds
                } ?: contract.controlFrameSeconds
                val vectors = stateVectors(state, gravity, dt, sessionGyroBias)
                val joints = vectors.second
                val jointVelocity = previousJointPosition?.let { previous ->
                    FloatArray(ACTION_COUNT) { (joints[it] - previous[it]) / dt }
                } ?: FloatArray(ACTION_COUNT)

                val target = request.get()
                val commandTarget = floatArrayOf(target.forward, 0f, target.yawRate)
                val alpha = (
                    contract.controlFrameSeconds / contract.commandSmoothingSeconds
                ).coerceAtMost(1f)
                command = FloatArray(3) { command[it] + alpha * (commandTarget[it] - command[it]) }
                val stateSequence = state.getLong("seq")
                val previousActionForObservation = requireNotNull(actionsBySequence[stateSequence]).copyOf()
                val inputAppliedTargets = requireNotNull(targetsBySequence[stateSequence])
                val frame = vectors.first + command + joints +
                    FloatArray(ACTION_COUNT) { 0.05f * jointVelocity[it] } +
                    previousActionForObservation
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
                actionsBySequence[sequence] = appliedAction.copyOf()
                targetsBySequence[sequence] = targets
                val commandSentNs = SystemClock.elapsedRealtimeNanos()
                send(RobotProtocol.policyFrame(sequence, targets))
                previousJointPosition = joints
                previousSampleMs = sampleMs
                state = coalescePolicyAngles(
                    next = awaitPolicyStateAfterTick(inputTick, FEEDBACK_TIMEOUT_MS),
                    previous = inputState,
                )
                val feedbackReceivedNs = SystemClock.elapsedRealtimeNanos()
                val feedbackSequence = state.getLong("seq")
                require(feedbackSequence <= sequence) {
                    "feedback sequence $feedbackSequence is ahead of command $sequence"
                }
                val feedbackTargets = requireNotNull(targetsBySequence[feedbackSequence]) {
                    "feedback referenced unknown command sequence $feedbackSequence"
                }
                val tracking = maximumTrackingError(state, feedbackTargets)
                peakTrackingError = maxOf(peakTrackingError, tracking.second)
                actionsBySequence.keys.removeAll { it < feedbackSequence - 2 }
                targetsBySequence.keys.removeAll { it < feedbackSequence - 2 }
                runCatching { recorder.recordDerivedFrame(
                    JSONObject()
                        .put("command_sequence", sequence)
                        .put("input_state_sequence", stateSequence)
                        .put("input_feedback_tick", inputTick)
                        .put("feedback_state_sequence", feedbackSequence)
                        .put("feedback_tick", state.getLong("tick"))
                        .put("firmware_sample_ms", sampleMs)
                        .put("dt_s", dt)
                        .put("command_target", commandTarget.jsonArray())
                        .put("command_applied", command.jsonArray())
                        .put("gyro_body_rad_s", vectors.first.copyOfRange(0, 3).jsonArray())
                        .put("projected_gravity_body", vectors.first.copyOfRange(3, 6).jsonArray())
                        .put("joint_position_rad", joints.jsonArray())
                        .put("joint_velocity_rad_s", jointVelocity.jsonArray())
                        .put("previous_applied_action", previousActionForObservation.jsonArray())
                        .put("observation", observation.jsonArray())
                        .put("requested_action", requestedAction.jsonArray())
                        .put("filtered_action", filteredAction.jsonArray())
                        .put("applied_action", appliedAction.jsonArray())
                        .put("policy_target_position_rad", policyPosition.jsonArray())
                        .put("servo_target_deg", targets.jsonObject())
                        .put("input_applied_servo_target_deg", inputAppliedTargets.jsonObject())
                        .put("tracking_error_deg", tracking.second)
                        .put("worst_tracking_servo_id", tracking.first)
                        .put("inference_ms", inferenceMs)
                        .put("frame_compute_ns", SystemClock.elapsedRealtimeNanos() - frameStartedNs)
                        .put("command_to_feedback_ns", feedbackReceivedNs - commandSentNs)
                        .put("input_robot_state", JSONObject(inputState.toString())),
                ) }
                mutableStatus.value = PolicyRuntimeStatus(
                    active = true,
                    commanding = true,
                    detail = "Continuous policy test running at 100% torque",
                    sequence = sequence,
                    inferenceMilliseconds = inferenceMs,
                    feedbackHertz = 1.0 / dt,
                    executionProvider = policy.executionProvider,
                    torquePercent = SERVO_TORQUE_LIMIT / 10,
                    gyroBiasDps = sessionGyroBias.copyOf(),
                    trackingErrorDegrees = tracking.second,
                    peakTrackingErrorDegrees = peakTrackingError,
                    worstTrackingServoId = tracking.first,
                )
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
                    "Policy not started: ${error.detail()}; stand torque remains on",
                )
            } else {
                safeDisarm("Policy stopped: ${error.detail()}")
            }
        } finally {
            runJob = null
        }
    }

    private suspend fun moveToCapturedPose() {
        mutableStatus.value = PolicyRuntimeStatus(
            active = true,
            detail = "Reading the current pose",
            executionProvider = policy.executionProvider,
        )
        try {
            val measured = readStableAngles(sampleCount = 1)
            val target = calibration.servoTargets(FloatArray(ACTION_COUNT))
            val trajectory = StandTrajectory.create(
                startByServoId = measured,
                targetByServoId = target,
                servoIds = calibration.servoIds.toList(),
                nominalStepDegrees = STAND_NOMINAL_STEP_DEG,
                maximumStepDegrees = STAND_MAXIMUM_STEP_DEG,
                minimumSteps = MIN_STAND_STEPS,
                maximumSteps = MAX_STAND_STEPS,
            )
            val poses = trajectory.poses
            recorder.recordEvent(
                "stand_trajectory",
                JSONObject()
                    .put("measured_start_deg", measured.jsonObject())
                    .put("target_deg", target.jsonObject())
                    .put("fractions", trajectory.fractions.jsonArray())
                    .put("step_count", poses.size)
                    .put("step_ms", STAND_STEP_MS)
                    .put("maximum_delta_deg", trajectory.maximumDeltaDegrees)
                    .put("maximum_step_deg", trajectory.maximumStepDegrees),
            )

            send(RobotProtocol.torqueLimitAll(SERVO_TORQUE_LIMIT))
            awaitMessage("ok", 2_000) { it.optString("cmd") == "servo_torque_limit" }
            send(RobotProtocol.torqueAll(true))
            awaitMessage("ok", 3_000) { it.optString("cmd") == "servo_torque" }
            // Upload small acknowledged records, then start them atomically.
            // A damaged or missing record cannot start partial motion.
            StandProgramUploader.uploadAndStart(
                poses = poses,
                stepMilliseconds = STAND_STEP_MS,
                speed = STAND_SPEED,
                acceleration = STAND_ACCELERATION,
            ) { command, expectedAcknowledgement ->
                send(command)
                awaitMessage("ok", 3_000) {
                    it.optString("cmd") == expectedAcknowledgement
                }
            }
            mutableStatus.value = mutableStatus.value.copy(
                detail = "Standing up",
                torquePercent = SERVO_TORQUE_LIMIT / 10,
            )
            delay(poses.size * STAND_STEP_MS.toLong())
            delay(STAND_SETTLE_MS)
            mutableStatus.value = PolicyRuntimeStatus(
                holdingPose = true,
                detail = "Standing; ready to run the trained policy",
                executionProvider = policy.executionProvider,
            )
            recorder.recordEvent("stand_complete")
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: Throwable) {
            safeDisarm("Stand failed: ${error.detail()}")
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
            val missingServoIds = (1..ACTION_COUNT).filter { !measured.has(it.toString()) }
            require(missingServoIds.isEmpty()) {
                "servo position feedback is missing for IDs ${missingServoIds.joinToString()}; " +
                    "connect and power the 7.4 V servo battery"
            }
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

    private suspend fun awaitPolicyStateAfterTick(previousTick: Long, timeoutMs: Long): JSONObject = withTimeout(timeoutMs) {
        while (true) {
            val message = messages.receive()
            when (message.optString("type")) {
                "error" -> error(message.optString("message", "ESP32 error"))
                "policy_disarmed" -> error(message.optString("reason", "firmware disarmed"))
                "policy_state" -> {
                    val tick = message.optLong("tick", -1)
                    if (tick <= previousTick) continue
                    require(message.optBoolean("armed", false)) { "firmware reports policy disarmed" }
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
        var attempts = 0
        while (gyroSamples.size < GYRO_ZERO_SAMPLE_COUNT && attempts < GYRO_ZERO_MAX_ATTEMPTS) {
            attempts += 1
            send(RobotProtocol.imuStatus())
            val sample = awaitMessage("imu", 2_000)
            if (sample.optBoolean("available", false)) {
                val vector = runCatching { sample.optJSONObject("gyro")?.vector3() }.getOrNull()
                if (vector != null) gyroSamples += vector
            }
            if (gyroSamples.size < GYRO_ZERO_SAMPLE_COUNT) delay(GYRO_ZERO_SAMPLE_INTERVAL_MS)
        }
        require(gyroSamples.size == GYRO_ZERO_SAMPLE_COUNT) {
            "ESP32 gyro did not return $GYRO_ZERO_SAMPLE_COUNT valid samples"
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
        runCatching { recorder.recordEvent("disarm_requested", JSONObject().put("detail", detail)) }
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
            feedbackHertz = previous.feedbackHertz,
            executionProvider = policy.executionProvider,
            torquePercent = previous.torquePercent,
            gyroBiasDps = previous.gyroBiasDps?.copyOf(),
            trackingErrorDegrees = previous.trackingErrorDegrees,
            peakTrackingErrorDegrees = previous.peakTrackingErrorDegrees,
            worstTrackingServoId = previous.worstTrackingServoId,
        )
        runCatching { recorder.finish("stopped", detail) }
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
            feedbackHertz = previous.feedbackHertz,
            executionProvider = policy.executionProvider,
            torquePercent = SERVO_TORQUE_LIMIT / 10,
        )
        runCatching {
            recorder.recordEvent("policy_start_failed_while_holding", JSONObject().put("detail", detail))
        }
    }

    override fun close() {
        stop("Runtime closed")
        policy.close()
    }

    companion object {
        private const val ACTION_COUNT = 12
        private const val HISTORY_COUNT = 4
        private const val FEEDBACK_TIMEOUT_MS = 80L
        private const val SERVO_TORQUE_LIMIT = 1000
        private const val TORQUE_ENABLE_ADDRESS = 0x28
        private const val CAPTURE_SAMPLE_INTERVAL_MS = 40L
        private const val CAPTURE_STABILITY_DEG = 1f
        private const val STAND_NOMINAL_STEP_DEG = 1.5f
        private const val STAND_MAXIMUM_STEP_DEG = 3f
        private const val MIN_STAND_STEPS = 12
        private const val MAX_STAND_STEPS = 24
        private const val STAND_STEP_MS = 80
        private const val STAND_SETTLE_MS = 500L
        private const val STAND_OPERATION_TIMEOUT_MS = 15_000L
        private const val STAND_SPEED = 360
        private const val STAND_ACCELERATION = 60
        private const val GYRO_ZERO_SAMPLE_COUNT = 25
        private const val GYRO_ZERO_MAX_ATTEMPTS = 40
        private const val GYRO_ZERO_SAMPLE_INTERVAL_MS = 20L
    }
}

private fun JSONObject.servoAnglesById(): Map<Int, Float> {
    val ids = getJSONArray("ids").intArray()
    val angles = getJSONArray("angles_deg").floatArray()
    require(ids.size == 12 && angles.size == 12)
    return ids.indices.associate { ids[it] to angles[it] }
}

internal fun coalescePolicyAngles(next: JSONObject, previous: JSONObject): JSONObject {
    if (next.optBoolean("feedback_complete", false)) return next
    val nextAngles = next.getJSONArray("angles_deg")
    val previousAngles = previous.getJSONArray("angles_deg")
    require(nextAngles.length() == 12 && previousAngles.length() == 12) {
        "policy feedback must contain 12 servo angles"
    }
    val merged = JSONObject(next.toString())
    val mergedAngles = merged.getJSONArray("angles_deg")
    repeat(12) { index ->
        if (mergedAngles.isNull(index)) mergedAngles.put(index, previousAngles.getDouble(index))
    }
    merged.put("feedback_coalesced", true)
    return merged
}

private fun JSONArray.floatArray(): FloatArray = FloatArray(length()) { index ->
    getDouble(index).toFloat().also { require(it.isFinite()) }
}

private fun JSONArray.intArray(): IntArray = IntArray(length()) { index -> getInt(index) }

private fun FloatArray.jsonArray(): JSONArray = JSONArray(toList())

private fun Map<Int, Float>.jsonObject(): JSONObject = JSONObject().also { output ->
    toSortedMap().forEach { (key, value) -> output.put(key.toString(), value) }
}

private fun JSONObject.vector3(): FloatArray = floatArrayOf(
    getDouble("x").toFloat(),
    getDouble("y").toFloat(),
    getDouble("z").toFloat(),
).also { require(it.all(Float::isFinite)) }

private fun Throwable.detail(): String = message?.takeIf(String::isNotBlank) ?: javaClass.simpleName
