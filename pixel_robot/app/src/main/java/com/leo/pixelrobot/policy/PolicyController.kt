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
    val detail: String = "Policy idle",
    val sequence: Long = 0,
    val inferenceMilliseconds: Double? = null,
    val executionProvider: String? = null,
)

class PolicyController(
    assets: AssetManager,
    private val scope: CoroutineScope,
    private val send: (ByteArray) -> Unit,
) : Closeable {
    private val calibration = RobotCalibration.load(assets)
    private val policy = OnnxPolicy(assets)
    private val messages = Channel<JSONObject>(capacity = 64, onBufferOverflow = BufferOverflow.DROP_OLDEST)
    private val request = AtomicReference(MotionRequest())
    private val mutableStatus = MutableStateFlow(
        PolicyRuntimeStatus(detail = "Actor ready", executionProvider = policy.executionProvider),
    )
    val status: StateFlow<PolicyRuntimeStatus> = mutableStatus.asStateFlow()
    private var runJob: Job? = null

    fun updateRequest(forward: Float, yawRate: Float) {
        require(forward.isFinite() && forward in 0f..MAX_FORWARD)
        require(yawRate.isFinite() && yawRate in -MAX_YAW..MAX_YAW)
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

    fun startSuspendedTest(durationSeconds: Float = 5f) {
        require(durationSeconds in 0.1f..30f)
        check(runJob?.isActive != true) { "policy is already running" }
        while (messages.tryReceive().isSuccess) Unit
        runJob = scope.launch { run(durationSeconds) }
    }

    fun stop(reason: String = "Stop requested") {
        request.set(MotionRequest())
        runJob?.cancel(CancellationException(reason))
        safeDisarm(reason)
    }

    private suspend fun run(durationSeconds: Float) {
        val gravity = GravityEstimator()
        var previousJointPosition: FloatArray? = null
        var previousSampleMs: Long? = null
        var history: Array<FloatArray>? = null
        var command = floatArrayOf(request.get().forward, 0f, request.get().yawRate)
        var previousAction = FloatArray(ACTION_COUNT)
        val actionsBySequence = mutableMapOf(0L to previousAction.copyOf())
        val startedNs = SystemClock.elapsedRealtimeNanos()
        var sequence = 0L

        mutableStatus.value = PolicyRuntimeStatus(
            active = true,
            detail = "Arming guarded firmware transport",
            executionProvider = policy.executionProvider,
        )
        try {
            send(RobotProtocol.arm())
            var state = awaitPolicyState(expectedSequence = 0, timeoutMs = 2_000)
            var nextTickNs = SystemClock.elapsedRealtimeNanos()
            while (currentCoroutineContext().isActive && (SystemClock.elapsedRealtimeNanos() - startedNs) < durationSeconds * 1_000_000_000L) {
                val sampleMs = state.getLong("sample_ms") and 0xffff_ffffL
                val dt = previousSampleMs?.let { previous ->
                    val elapsed = (sampleMs - previous) and 0xffff_ffffL
                    if (elapsed in 5..100) elapsed / 1000f else FRAME_DT
                } ?: FRAME_DT
                val vectors = stateVectors(state, gravity, dt)
                val joints = vectors.second
                val jointVelocity = previousJointPosition?.let { previous ->
                    FloatArray(ACTION_COUNT) { (joints[it] - previous[it]) / dt }
                } ?: FloatArray(ACTION_COUNT)

                val target = request.get()
                val commandTarget = floatArrayOf(target.forward, 0f, target.yawRate)
                val alpha = (FRAME_DT / COMMAND_SMOOTHING_SECONDS).coerceAtMost(1f)
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
                val appliedAction = FloatArray(ACTION_COUNT) { index ->
                    requestedAction[index].coerceIn(
                        previousAction[index] - ACTION_DELTA_LIMIT,
                        previousAction[index] + ACTION_DELTA_LIMIT,
                    )
                }
                val policyPosition = FloatArray(ACTION_COUNT) { ACTION_SCALE_RADIANS * appliedAction[it] }
                val targets = calibration.servoTargets(policyPosition)

                sequence += 1
                actionsBySequence[sequence] = appliedAction
                previousAction = appliedAction
                send(RobotProtocol.policyFrame(sequence, targets))
                previousJointPosition = joints
                previousSampleMs = sampleMs
                actionsBySequence.keys.removeAll { it < sequence - 2 }
                mutableStatus.value = PolicyRuntimeStatus(
                    active = true,
                    detail = "Suspended policy test running",
                    sequence = sequence,
                    inferenceMilliseconds = inferenceMs,
                    executionProvider = policy.executionProvider,
                )

                state = awaitPolicyState(sequence, FEEDBACK_TIMEOUT_MS)
                nextTickNs += FRAME_PERIOD_NS
                val remainingNs = nextTickNs - SystemClock.elapsedRealtimeNanos()
                if (remainingNs > 0) delay((remainingNs + 999_999L) / 1_000_000L)
            }
            safeDisarm("Suspended test completed")
        } catch (timeout: TimeoutCancellationException) {
            safeDisarm("Policy stopped: feedback timeout")
        } catch (cancelled: CancellationException) {
            throw cancelled
        } catch (error: Throwable) {
            safeDisarm("Policy stopped: ${error.message}")
        } finally {
            runJob = null
        }
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

    private fun stateVectors(state: JSONObject, gravity: GravityEstimator, dt: Float): Pair<FloatArray, FloatArray> {
        val ids = state.getJSONArray("ids").intArray()
        val angles = state.getJSONArray("angles_deg").floatArray()
        require(ids.size == ACTION_COUNT && angles.size == ACTION_COUNT)
        val joints = calibration.policyPositions(ids.indices.associate { ids[it] to angles[it] })
        val gyroSensor = state.getJSONArray("gyro_dps").floatArray()
        val accelerationSensor = state.getJSONArray("accel_mg").floatArray()
        val gyroBody = calibration.bodyGyroRadPerSecond(gyroSensor)
        val accelerationBody = calibration.bodyAccelerationMg(accelerationSensor)
        val projectedGravity = gravity.update(accelerationBody, gyroBody, calibration.gravitySign, dt)
        return (gyroBody + projectedGravity) to joints
    }

    private fun safeDisarm(detail: String) {
        runCatching { send(RobotProtocol.command("stop")) }
        runCatching { send(RobotProtocol.command("policy_disarm")) }
        mutableStatus.value = PolicyRuntimeStatus(
            active = false,
            detail = detail,
            sequence = mutableStatus.value.sequence,
            inferenceMilliseconds = mutableStatus.value.inferenceMilliseconds,
            executionProvider = policy.executionProvider,
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
        private const val ACTION_DELTA_LIMIT = 0.30f
        private const val ACTION_SCALE_RADIANS = 0.25f
        private const val COMMAND_SMOOTHING_SECONDS = 0.40f
        private const val MAX_FORWARD = 0.18f
        private const val MAX_YAW = 0.25f
    }
}

private fun JSONArray.floatArray(): FloatArray = FloatArray(length()) { index ->
    getDouble(index).toFloat().also { require(it.isFinite()) }
}

private fun JSONArray.intArray(): IntArray = IntArray(length()) { index -> getInt(index) }
