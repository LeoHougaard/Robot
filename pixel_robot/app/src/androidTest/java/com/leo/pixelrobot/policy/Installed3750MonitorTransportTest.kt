package com.leo.pixelrobot.policy

import android.content.Context
import android.hardware.usb.UsbManager
import android.os.SystemClock
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.leo.pixelrobot.robot.JsonLineDecoder
import com.leo.pixelrobot.robot.RobotProtocol
import com.leo.pixelrobot.robot.RunSessionRecorder
import com.leo.pixelrobot.robot.UsbRobotTransport
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicInteger
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Explicit hardware diagnostic: no arm, torque, configuration or motion commands. */
@RunWith(AndroidJUnit4::class)
class Installed3750MonitorTransportTest {
    @Test
    fun sampledSensorsInferenceRecordingAndUsbMeet50Hz() {
        assumeTrue(InstrumentationRegistry.getArguments().getString("robot_hardware") == "torque_off")
        assumeTrue(InstrumentationRegistry.getArguments().getString("policy_candidate") == "epoch3750")
        val context = ApplicationProvider.getApplicationContext<Context>()
        val candidateAssets = context.assets
        val manager = context.getSystemService(UsbManager::class.java)
        val device = manager.deviceList.values.single {
            it.vendorId == UsbRobotTransport.ROBOT_VENDOR_ID && it.productId == UsbRobotTransport.ROBOT_PRODUCT_ID
        }
        check(manager.hasPermission(device)) { "Grant USB permission in Pixel Robot, then stop the app before this test" }
        val candidateAssetPrefix = ""
        val candidateMetadata = candidateAssets.open("policy_metadata.json")
            .bufferedReader().use { it.readText() }
        val metadataJson = JSONObject(candidateMetadata)
        check(metadataJson.getInt("checkpoint_epoch") == 3750)
        check(metadataJson.getString("checkpoint_sha256") ==
            "964b3d4fc7e40ce841e1ef6167f162d0af89d8f9f8057757bf07e16ba79ea8b5")
        val contract = PolicyContract.parse(candidateMetadata)
        check(contract.observationSize == 428)
        check(contract.jointCoordinateConvention == "cad_drives_v1")
        fun sha256(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256")
            .digest(bytes).joinToString("") { "%02x".format(it.toInt() and 0xff) }
        val calibrationBytes = candidateAssets.open("assembly-four-leg-linkage-12dof.calibration.json")
            .use { it.readBytes() }
        check(sha256(calibrationBytes) == "6d5cccade24a54c8ff98e7e194f41af676c9f6268be15e44d9293b649090bfa6")
        val calibration = RobotCalibration.parse(calibrationBytes.toString(Charsets.UTF_8))
        val failure = AtomicReference<Throwable?>()
        val helloReceived = AtomicBoolean(false)
        val startupFragments = AtomicInteger(0)
        val queue = ArrayBlockingQueue<Pair<JSONObject, Long>>(8)
        val normalErrors = mutableListOf<String>()
        val firmwareDiagnostics = mutableListOf<JSONObject>()
        val decoder = JsonLineDecoder()
        val recorder = RunSessionRecorder(File(context.filesDir, "transport_diagnostics"), {
            JSONObject().put("mode", "torque_off_no_motor_writes")
                .put("weights_sha256", contract.weightsSha256).put("observation_size", contract.observationSize)
        })
        check(recorder.start("motor_disabled_transport").active)
        val transport = UsbRobotTransport(manager, device, { bytes ->
            val receivedNs = SystemClock.elapsedRealtimeNanos()
            runCatching {
                decoder.accept(bytes).forEach { line ->
                    val message = try { JSONObject(line) } catch (error: Exception) {
                        // Opening an already-streaming UART can start midway
                        // through a line. Only tolerate this before handshake.
                        if (helloReceived.get()) throw error
                        startupFragments.incrementAndGet()
                        return@forEach
                    }
                    if (message.optString("type") == "hello") helloReceived.set(true)
                    recorder.recordRobotRx(message)
                    if (message.optString("type") in setOf("hello", "ok", "error", "policy_monitor_state", "policy_monitor_stopped")) {
                        check(queue.offer(message to receivedNs)) { "hardware feedback queue overflow" }
                    }
                }
            }.onFailure { failure.compareAndSet(null, it) }
        }, { failure.compareAndSet(null, it) })
        fun send(message: JSONObject) {
            val bytes = (message.toString() + "\n").toByteArray()
            recorder.recordRobotTx(bytes)
            transport.write(bytes)
        }
        fun receive(type: String): Pair<JSONObject, Long> {
            val until = SystemClock.elapsedRealtime() + 2000
            var retryHelloAt = SystemClock.elapsedRealtime() + 250
            while (SystemClock.elapsedRealtime() < until) {
                failure.get()?.let { throw it }
                if (type == "hello" && SystemClock.elapsedRealtime() >= retryHelloAt) {
                    send(JSONObject().put("cmd", "hello"))
                    retryHelloAt = SystemClock.elapsedRealtime() + 250
                }
                val item = queue.poll(100, TimeUnit.MILLISECONDS) ?: continue
                if (item.first.optString("type") == "error") {
                    normalErrors += item.first.toString()
                    continue
                }
                if (item.first.optString("type") == type) return item
                check(item.first.optString("type") != "policy_monitor_stopped") { item.first.toString() }
            }
            error("no $type received")
        }
        var outcome = "failed"
        var loadedPolicy: OnnxPolicy? = null
        try {
            val policy = OnnxPolicy(candidateAssets, contract.profileId, contract.profileSha256,
                contract.weightsSha256, contract.observationSize, candidateAssetPrefix)
            loadedPolicy = policy
            transport.open()
            send(JSONObject().put("cmd", "hello"))
            val hello = receive("hello").first
            check(!hello.getBoolean("policyArmed") && !hello.optBoolean("policyMonitoring"))
            check(hello.optBoolean("supportsTorqueOffPolicyMonitor")) { "Flash firmware 0.1.15 or later first" }
            check(hello.getInt("policyFeedbackIntervalMs") == 20)
            send(JSONObject().put("cmd", "policy_monitor").put("duration_ms", 30000))
            check(receive("ok").first.getString("cmd") == "policy_monitor")
            val sensors = PolicySensors(calibration, .02f)
            val reference = candidateAssets.open("stride_reference.json")
                .use { it.readBytes() }
            check(sha256(reference) == "b2db6928c7aa3d7acb521a5f254250807a371f2eaac75783ff087b0240217fc2")
            val session = PolicyFrameSession(contract, reference)
            val command = floatArrayOf(.08f.coerceAtMost(contract.forwardMaximum), 0f, 0f)
            val posture = FloatArray(3)
            var applied = FloatArray(12)
            val actions = mutableMapOf(0L to applied.copyOf())
            val sampleIntervals = mutableListOf<Double>()
            val hostIntervals = mutableListOf<Double>()
            val computeTimes = mutableListOf<Double>()
            var previousHost = 0L
            var previousTick = 0L
            var currentComplete = 0
            session.reset()
            repeat(1250) { index ->
                    val (state, hostNs) = receive("policy_monitor_state")
                    val computeStart = SystemClock.elapsedRealtimeNanos()
                    if (state.optBoolean("armed") || !state.optBoolean("feedback_complete")) normalErrors += "state_not_complete:$index"
                    if (state.getLong("missed_feedback_periods") != 0L) normalErrors += "missed_feedback:$index"
                    if (state.getLong("tick") != previousTick + 1) normalErrors += "tick_gap:$index"
                    firmwareDiagnostics += JSONObject(state.toString())
                    previousTick = state.getLong("tick")
                    val ack = state.getLong("seq")
                    if (ack !in maxOf(0L, index.toLong() - 2)..index.toLong()) normalErrors += "ack_stalled:$index"
                    val sample = sensors.read(state, calibration.gyroBiasDps)
                    val observation = session.observation(sample, command, posture, checkNotNull(actions[ack]))
                    val requested = policy.action(observation)
                    val next = session.action(requested, command, posture, sample.imu.copyOfRange(3, 6))
                    applied = next.applied
                    val targets = calibration.servoTargets(FloatArray(12) { contract.positionTargetScaleRadians * applied[it] })
                    val sequence = index.toLong() + 1
                    actions[sequence] = applied.copyOf()
                    actions.keys.removeAll { it < ack - 2 }
                    // Identical target decoding, but firmware never calls the motor write path for this command.
                    val packet = JSONObject(RobotProtocol.policyFrame(sequence, targets).toString(Charsets.UTF_8))
                    packet.put("cmd", "policy_monitor_frame")
                    send(packet)
                    recorder.recordDerivedFrame(JSONObject().put("observation", JSONArray(observation.toList()))
                        .put("requested_action", JSONArray(requested.toList())).put("input_robot_state", state))
                    session.completeFrame()
                    if (index >= 50) {
                        sampleIntervals += sample.dt.toDouble() * 1000
                        hostIntervals += (hostNs - previousHost) / 1e6
                        computeTimes += (SystemClock.elapsedRealtimeNanos() - computeStart) / 1e6
                        if (state.getBoolean("current_complete")) currentComplete++
                    }
                    previousHost = hostNs
            }
            fun percentile(values: List<Double>, fraction: Double) = values.sorted()[((values.size - 1) * fraction).toInt()]
            val report = JSONObject().put("firmware", hello.getString("version"))
                .put("startup_partial_lines", startupFragments.get())
                .put("normal_error_count", normalErrors.size)
                .put("normal_errors", JSONArray(normalErrors))
                .put("firmware_diagnostic_count", firmwareDiagnostics.size)
                .put("firmware_diagnostic_last", firmwareDiagnostics.lastOrNull() ?: JSONObject())
                .put("weights_sha256", contract.weightsSha256).put("measured_frames", sampleIntervals.size)
                .put("firmware_hz", 1000 / sampleIntervals.average()).put("host_hz", 1000 / hostIntervals.average())
                .put("sample_p99_ms", percentile(sampleIntervals, .99)).put("sample_max_ms", sampleIntervals.max())
                .put("compute_p99_ms", percentile(computeTimes, .99)).put("current_complete_fraction", currentComplete / 1200.0)
                .put("motor_targets_written", false).put("physical_walking_verified", false)
            File(context.filesDir, "motor-disabled-transport-result.json").writeText(report.toString(2))
            assertTrue(report.toString(), report.getDouble("firmware_hz") in 49.5..50.5)
            assertTrue(report.toString(), report.getDouble("host_hz") in 49.5..50.5)
            assertTrue(report.toString(), report.getDouble("sample_p99_ms") <= 25 && report.getDouble("sample_max_ms") <= 40)
            assertTrue(report.toString(), report.getDouble("compute_p99_ms") < 10)
            assertTrue(report.toString(), report.getDouble("current_complete_fraction") >= .99)
            outcome = "passed"
        } finally {
            SystemClock.sleep(250)
            runCatching {
                send(JSONObject().put("cmd", "policy_disarm"))
                receive("policy_monitor_stopped")
            }
            transport.close()
            loadedPolicy?.close()
            val saved = recorder.finish(outcome, "read-only feedback, inference and USB; no motor target writes")
            recorder.close()
            check(saved.error == null) { "transport recording incomplete: ${saved.error}" }
        }
    }
}

