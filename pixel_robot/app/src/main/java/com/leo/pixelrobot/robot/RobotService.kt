package com.leo.pixelrobot.robot

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.ServiceInfo
import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import android.os.BatteryManager
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import com.leo.pixelrobot.MainActivity
import com.leo.pixelrobot.R
import com.leo.pixelrobot.policy.PolicyController
import com.leo.pixelrobot.policy.PolicyContract
import com.leo.pixelrobot.policy.PolicyRuntimeStatus
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.io.File

class RobotService : Service() {
    inner class LocalBinder : Binder() {
        val service: RobotService get() = this@RobotService
    }

    private val binder = LocalBinder()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val decoder = JsonLineDecoder()
    private val mutableStatus = MutableStateFlow(RobotStatus())
    val status: StateFlow<RobotStatus> = mutableStatus.asStateFlow()

    private lateinit var usbManager: UsbManager
    private lateinit var policyController: PolicyController
    private lateinit var policyContract: PolicyContract
    private lateinit var runRecorder: RunSessionRecorder
    private lateinit var trainingCaptureBundle: TrainingCaptureBundle
    private lateinit var effectiveCalibrationJson: String
    private lateinit var calibrationSource: String
    private var controlServer: RobotControlServer? = null
    private var transport: UsbRobotTransport? = null
    private var connectedDeviceId: Int? = null
    @Volatile private var openingDeviceId: Int? = null
    private var permissionRequestDeviceId: Int? = null
    private var isForeground = false
    private var foregroundConnectedDevice = false
    private var lastForegroundText: String? = null
    private var handshakeJob: Job? = null
    private var retryJob: Job? = null
    private var sessionMonitorJob: Job? = null
    private var servoTelemetryJob: Job? = null
    @Volatile private var selectedServoId: Int? = null

    private val usbReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (intent.action) {
                ACTION_USB_PERMISSION -> {
                    permissionRequestDeviceId = null
                    val device = intent.usbDevice()
                    if (device != null && intent.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED, false)) {
                        open(device)
                    } else {
                        setStatus(LinkState.PERMISSION_REQUIRED, "USB permission was denied")
                    }
                }

                UsbManager.ACTION_USB_DEVICE_ATTACHED -> discoverRobot()
                UsbManager.ACTION_USB_DEVICE_DETACHED -> {
                    val device = intent.usbDevice()
                    if (device?.deviceId == connectedDeviceId) disconnect("ESP32 disconnected; firmware watchdog will hold")
                }
            }
        }
    }

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        usbManager = getSystemService(UsbManager::class.java)
        startInForeground(connectedDevice = false)
        policyContract = PolicyContract.load(assets)
        val profileId = policyContract.profileId
        val calibrationOverrideFile = File(filesDir, "$profileId.calibration.json")
        val calibrationOverride = calibrationOverrideFile.takeIf(File::isFile)?.readText(Charsets.UTF_8)
        calibrationSource = if (calibrationOverride != null) "app_override" else "bundled_asset"
        effectiveCalibrationJson = calibrationOverride
            ?: assets.open("$profileId.calibration.json").bufferedReader().use { it.readText() }
        runRecorder = RunSessionRecorder(
            directory = File(filesDir, "run_sessions"),
            contextProvider = ::runSessionContext,
        )
        trainingCaptureBundle = TrainingCaptureBundle(
            directory = File(filesDir, "training_captures"),
            bundledFiles = {
                linkedMapOf(
                    "policy/policy_actor.onnx" to assets.open("policy_actor.onnx").use { it.readBytes() },
                    "policy/policy_metadata.json" to assets.open("policy_metadata.json").use { it.readBytes() },
                    "policy/policy_android_manifest.json" to assets.open("policy_android_manifest.json").use { it.readBytes() },
                    "policy/policy_reference.json" to assets.open("policy_reference.json").use { it.readBytes() },
                    "calibration/${policyContract.profileId}.calibration.json" to
                        effectiveCalibrationJson.toByteArray(Charsets.UTF_8),
                )
            },
            manifestContext = {
                JSONObject()
                    .put("profile_id", policyContract.profileId)
                    .put("calibration_source", calibrationSource)
                    .put("app_package", packageName)
            },
        )
        policyController = PolicyController(
            assets = assets,
            scope = scope,
            calibrationOverrideJson = calibrationOverride,
            recorder = runRecorder,
            send = { bytes ->
                runCatching { runRecorder.recordRobotTx(bytes) }
                transport?.write(bytes) ?: error("ESP32 USB link is not ready")
            },
        )
        controlServer = runCatching {
            RobotControlServer(
                assets = assets,
                status = ::controlStatus,
                updateCommand = ::updateMotionRequest,
                stand = ::standAtCapturedPose,
                startTest = ::startPolicy,
                stop = ::emergencyStop,
                reconnect = ::reconnect,
                selectServoTelemetry = ::selectServoTelemetry,
                latestSession = runRecorder::latestCompletedFile,
                startRecording = ::startRecording,
                stopRecording = ::stopRecording,
                latestTrainingCapture = ::latestTrainingCaptureFile,
            ).also(RobotControlServer::start)
        }.onFailure { Log.e(TAG, "Could not start loopback control bridge", it) }.getOrNull()
        val receiverFilter = IntentFilter().apply {
            addAction(ACTION_USB_PERMISSION)
            addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
            addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
        }
        ContextCompat.registerReceiver(this, usbReceiver, receiverFilter, ContextCompat.RECEIVER_NOT_EXPORTED)
        startServoTelemetryMonitor()
        discoverRobot()
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_RECONNECT -> reconnect()
            ACTION_STOP -> emergencyStop()
        }
        return START_STICKY
    }

    fun reconnect() {
        retryJob?.cancel()
        permissionRequestDeviceId = null
        if (policyController.status.value.active) policyController.stop("USB reconnect requested")
        if (transport != null) {
            decoder.reset()
            setStatus(LinkState.HANDSHAKING, "Checking ESP32 link")
            startHandshake()
        } else {
            discoverRobot()
        }
    }

    private fun discoverRobot() {
        if (transport != null || openingDeviceId != null) return
        val device = findRobotDevice()
        if (device == null) {
            setStatus(LinkState.SEARCHING, "Connect the ESP32 over USB-C")
            scheduleRetry()
            return
        }
        if (!usbManager.hasPermission(device)) {
            if (permissionRequestDeviceId == device.deviceId) return
            permissionRequestDeviceId = device.deviceId
            setStatus(LinkState.PERMISSION_REQUIRED, "Waiting for USB permission", device.deviceName)
            val permissionIntent = Intent(ACTION_USB_PERMISSION).setPackage(packageName)
            val pending = PendingIntent.getBroadcast(
                this,
                0,
                permissionIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
            )
            usbManager.requestPermission(device, pending)
            return
        }
        open(device)
    }

    fun emergencyStop() {
        policyController.stop("Stop requested; torque-off sent")
        mutableStatus.value = mutableStatus.value.copy(policyArmed = false, detail = "Stop requested; torque-off sent")
    }

    val policyStatus: StateFlow<PolicyRuntimeStatus> get() = policyController.status
    val forwardMinimum: Float get() = policyContract.forwardMinimum
    val forwardMaximum: Float get() = policyContract.forwardMaximum
    val yawMinimum: Float get() = policyContract.yawMinimum
    val yawMaximum: Float get() = policyContract.yawMaximum
    val supportsPostureCommands: Boolean get() = policyContract.observationBuilder == "current_v3_279"
    val postureHeightMinimum: Float get() = policyContract.postureHeightMinimum
    val postureHeightMaximum: Float get() = policyContract.postureHeightMaximum
    val postureRollMinimum: Float get() = policyContract.postureRollMinimum
    val postureRollMaximum: Float get() = policyContract.postureRollMaximum
    val posturePitchMinimum: Float get() = policyContract.posturePitchMinimum
    val posturePitchMaximum: Float get() = policyContract.posturePitchMaximum
    fun recordingStatus(): RunRecordingStatus = runRecorder.status()
    fun latestRunFile(): File? = runRecorder.latestCompletedFile()
    fun latestTrainingCaptureFile(): File? = runRecorder.latestCompletedFile()?.let(trainingCaptureBundle::create)

    fun startRecording() {
        val status = runRecorder.start("operator_recording")
        check(status.active) { status.error ?: "recording could not start" }
        runRecorder.recordEvent("operator_recording_started")
        startSessionMonitor()
    }

    fun stopRecording() {
        val status = runRecorder.finish("operator_stop", "Recording stopped by operator")
        check(!status.active) { status.error ?: "recording could not stop" }
    }

    fun updateMotionRequest(forward: Float, yawRate: Float, lateral: Float = 0f) {
        policyController.updateRequest(forward, yawRate, lateral)
    }

    fun updatePostureRequest(heightOffset: Float, roll: Float, pitch: Float) {
        policyController.updatePosture(heightOffset, roll, pitch)
    }

    private fun firmwareSupportsInstalledPolicy(version: String?): Boolean =
        if (policyContract.observationBuilder == "current_body_v20_426") {
            FirmwareCapabilities.supportsStablePolicyFeedback(version)
        } else FirmwareCapabilities.supportsClockedPolicyFeedback(version)

    fun startPolicy() {
        check(mutableStatus.value.linkState == LinkState.READY) { "ESP32 is not ready" }
        check(firmwareSupportsInstalledPolicy(mutableStatus.value.firmwareVersion)) {
            "ESP32 firmware ${if (policyContract.observationBuilder == "current_body_v20_426") "0.1.14" else "0.1.13"} or newer is required for this policy"
        }
        policyController.startPolicy()
        startSessionMonitor()
    }

    fun standAtCapturedPose() {
        check(mutableStatus.value.linkState == LinkState.READY) { "ESP32 is not ready" }
        policyController.standAtCapturedPose()
        startSessionMonitor()
    }

    fun selectServoTelemetry(id: Int?) {
        require(id == null || id in 1..12) { "servo ID must be from 1 through 12" }
        selectedServoId = id
        val current = mutableStatus.value
        mutableStatus.value = current.copy(
            selectedServoId = id,
            servoTelemetry = id?.let(current.servoTelemetryById::get),
        )
    }

    private fun startServoTelemetryMonitor() {
        if (servoTelemetryJob?.isActive == true) return
        servoTelemetryJob = scope.launch {
            while (isActive) {
                val id = selectedServoId
                val robot = mutableStatus.value
                val policy = policyController.status.value
                if (
                    id != null &&
                    robot.linkState == LinkState.READY &&
                    FirmwareCapabilities.supportsPolicyServoTelemetry(robot.firmwareVersion) &&
                    policy.holdingPose &&
                    !policy.active
                ) {
                    runCatching { transport?.write(RobotProtocol.servoTelemetry(id)) }
                }
                delay(SERVO_TELEMETRY_INTERVAL_MS)
            }
        }
    }

    private fun startSessionMonitor() {
        if (sessionMonitorJob?.isActive == true) return
        sessionMonitorJob = scope.launch {
            while (isActive && runRecorder.status().active) {
                runCatching { runRecorder.recordEvent("android_system_sample", androidSystemSnapshot()) }
                delay(1_000)
            }
        }
    }

    private fun androidSystemSnapshot(): JSONObject {
        val battery = registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val runtime = Runtime.getRuntime()
        val robot = mutableStatus.value
        val servoBattery = ServoBatterySafety.evaluate(
            robot.servoBatteryVoltage,
            robot.servoBatteryLive,
        )
        return JSONObject()
            .put(
                "thermal_status",
                getSystemService(PowerManager::class.java).currentThermalStatus,
            )
            .put("battery_percent", battery?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1)
            .put("battery_scale", battery?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1)
            .put("battery_voltage_mv", battery?.getIntExtra(BatteryManager.EXTRA_VOLTAGE, -1) ?: -1)
            .put(
                "battery_temperature_tenths_c",
                battery?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -1) ?: -1,
            )
            .put("battery_plugged", battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) ?: 0)
            .put("runtime_heap_used_bytes", runtime.totalMemory() - runtime.freeMemory())
            .put("runtime_heap_max_bytes", runtime.maxMemory())
            .put("storage_free_bytes", filesDir.freeSpace)
            .put("storage_total_bytes", filesDir.totalSpace)
            .put("servo_battery_voltage", servoBattery.voltage ?: JSONObject.NULL)
            .put("servo_battery_live", servoBattery.live)
            .put("servo_battery_level", servoBattery.level.name.lowercase())
    }

    private fun controlStatus(): JSONObject {
        val robot = mutableStatus.value
        val policy = policyController.status.value
        val recording = runRecorder.status()
        val servoBattery = ServoBatterySafety.evaluate(
            robot.servoBatteryVoltage,
            robot.servoBatteryLive,
        )
        val telemetry = robot.selectedServoId?.let(robot.servoTelemetryById::get)
        val telemetryLive = telemetry != null &&
            robot.linkState == LinkState.READY &&
            SystemClock.elapsedRealtime() - telemetry.receivedAtMs <= SERVO_TELEMETRY_LIVE_MS
        val telemetryMessage = when {
            robot.selectedServoId == null -> "Choose one servo ID"
            robot.linkState != LinkState.READY -> "Connect the ESP32 to read servo load"
            !FirmwareCapabilities.supportsPolicyServoTelemetry(robot.firmwareVersion) ->
                "Update ESP32 firmware ${robot.firmwareVersion ?: "unknown"} to 0.1.12"
            telemetryLive && policy.active -> "Live current on every policy frame for all 12 servos"
            telemetryLive -> "Live servo load"
            policy.active -> "Waiting for the next policy sample for servo ${robot.selectedServoId}"
            !policy.holdingPose -> "Stand or run the policy to read servo load"
            else -> "Waiting for servo ${robot.selectedServoId}"
        }
        val allTelemetry = JSONObject().also { json ->
            robot.servoTelemetryById.toSortedMap().forEach { (id, sample) ->
                json.put(id.toString(), sample.toJson())
            }
        }
        return JSONObject()
            .put("link_state", robot.linkState.name.lowercase())
            .put("robot_detail", robot.detail)
            .put("firmware_version", robot.firmwareVersion)
            .put("policy_armed", robot.policyArmed)
            .put("feedback_complete", robot.feedbackComplete)
            .put("servo_battery_voltage", robot.servoBatteryVoltage ?: JSONObject.NULL)
            .put("servo_battery_live", robot.servoBatteryLive)
            .put("servo_battery_level", servoBattery.level.name.lowercase())
            .put("servo_battery_message", servoBattery.message())
            .put("servo_battery_warning_voltage", ServoBatterySafety.WARNING_VOLTAGE)
            .put("servo_battery_critical_voltage", ServoBatterySafety.CRITICAL_VOLTAGE)
            .put("servo_telemetry_selected_id", robot.selectedServoId ?: JSONObject.NULL)
            .put("servo_telemetry_live", telemetryLive)
            .put("servo_telemetry_message", telemetryMessage)
            .put("servo_telemetry", telemetry?.toJson() ?: JSONObject.NULL)
            .put("servo_telemetry_all", allTelemetry)
            .put("policy_active", policy.active)
            .put("policy_commanding", policy.commanding)
            .put("holding_pose", policy.holdingPose)
            .put("policy_detail", policy.detail)
            .put("sequence", policy.sequence)
            .put("inference_ms", policy.inferenceMilliseconds)
            .put("feedback_hz", policy.feedbackHertz)
            .put(
                "clocked_policy_ready",
                firmwareSupportsInstalledPolicy(robot.firmwareVersion),
            )
            .put("execution_provider", policy.executionProvider)
            .put("torque_percent", policy.torquePercent ?: JSONObject.NULL)
            .put(
                "gyro_bias_dps",
                policy.gyroBiasDps?.let { JSONArray(it.toList()) } ?: JSONObject.NULL,
            )
            .put("tracking_error_deg", policy.trackingErrorDegrees ?: JSONObject.NULL)
            .put("peak_tracking_error_deg", policy.peakTrackingErrorDegrees ?: JSONObject.NULL)
            .put("worst_tracking_servo_id", policy.worstTrackingServoId ?: JSONObject.NULL)
            .put("forward_min", policyContract.forwardMinimum)
            .put("forward_max", policyContract.forwardMaximum)
            .put("lateral_min", policyContract.lateralMinimum)
            .put("lateral_max", policyContract.lateralMaximum)
            .put("yaw_min", policyContract.yawMinimum)
            .put("yaw_max", policyContract.yawMaximum)
            .put("recording_active", recording.active)
            .put("recording_file", recording.activeFileName ?: JSONObject.NULL)
            .put("recording_records", recording.recordCount)
            .put("recording_bytes", recording.bytesWritten)
            .put("recording_error", recording.error ?: JSONObject.NULL)
            .put("latest_session_file", recording.latestFileName ?: JSONObject.NULL)
            .put("latest_session_available", runRecorder.latestCompletedFile() != null)
    }

    private fun runSessionContext(): JSONObject {
        val packageInfo = packageManager.getPackageInfo(packageName, 0)
        val robot = mutableStatus.value
        val servoBattery = ServoBatterySafety.evaluate(
            robot.servoBatteryVoltage,
            robot.servoBatteryLive,
        )
        return JSONObject()
            .put(
                "app",
                JSONObject()
                    .put("package", packageName)
                    .put("version_name", packageInfo.versionName)
                    .put("version_code", packageInfo.longVersionCode),
            )
            .put(
                "android_device",
                JSONObject()
                    .put("manufacturer", Build.MANUFACTURER)
                    .put("model", Build.MODEL)
                    .put("device", Build.DEVICE)
                    .put("android_release", Build.VERSION.RELEASE)
                    .put("sdk", Build.VERSION.SDK_INT),
            )
            .put(
                "robot_link",
                JSONObject()
                    .put("firmware_version", robot.firmwareVersion ?: JSONObject.NULL)
                    .put("usb_device", robot.deviceName ?: JSONObject.NULL)
                    .put("servo_battery_voltage", robot.servoBatteryVoltage ?: JSONObject.NULL)
                    .put("servo_battery_level", servoBattery.level.name.lowercase()),
            )
            .put(
                "servo_battery_safety",
                JSONObject()
                    .put("cell_count", 2)
                    .put("warning_voltage", ServoBatterySafety.WARNING_VOLTAGE)
                    .put("critical_voltage", ServoBatterySafety.CRITICAL_VOLTAGE)
                    .put("warning_only", true),
            )
            .put(
                "policy_metadata",
                JSONObject(assets.open("policy_metadata.json").bufferedReader().use { it.readText() }),
            )
            .put(
                "policy_android_manifest",
                JSONObject(assets.open("policy_android_manifest.json").bufferedReader().use { it.readText() }),
            )
            .put("calibration_source", calibrationSource)
            .put("calibration", JSONObject(effectiveCalibrationJson))
    }

    private fun open(device: UsbDevice) {
        synchronized(this) {
            if (transport != null || openingDeviceId != null) return
            openingDeviceId = device.deviceId
        }
        setStatus(LinkState.OPENING, "Opening CP210x at ${UsbRobotTransport.BAUD} baud", device.deviceName)
        scope.launch {
            try {
                val opened = UsbRobotTransport(usbManager, device, ::onUsbBytes, ::onUsbFailure)
                opened.open()
                startInForeground(connectedDevice = true)
                transport = opened
                connectedDeviceId = device.deviceId
                decoder.reset()
                setStatus(LinkState.HANDSHAKING, "Waiting for ESP32 hello", device.deviceName)
                startHandshake()
            } catch (error: Throwable) {
                disconnect("USB open failed: ${error.message}")
                scheduleRetry()
            } finally {
                openingDeviceId = null
            }
        }
    }

    private fun startHandshake() {
        handshakeJob?.cancel()
        handshakeJob = scope.launch {
            val deadline = SystemClock.elapsedRealtime() + HANDSHAKE_TIMEOUT_MS
            var slowStartReported = false
            while (isActive && transport != null && mutableStatus.value.linkState == LinkState.HANDSHAKING) {
                if (!slowStartReported && SystemClock.elapsedRealtime() >= deadline) {
                    slowStartReported = true
                    setStatus(
                        LinkState.HANDSHAKING,
                        "ESP32 is still starting; keeping the USB port open",
                    )
                }
                runCatching { transport?.write(RobotProtocol.command("hello")) }
                    .onFailure {
                        disconnect("USB handshake failed: ${it.message}")
                        scheduleRetry()
                        return@launch
                    }
                delay(250)
            }
        }
    }

    private fun onUsbBytes(bytes: ByteArray) {
        decoder.accept(bytes).forEach { line ->
            runCatching { JSONObject(line) }.onSuccess(::onMessage)
        }
    }

    private fun onMessage(message: JSONObject) {
        policyController.onRobotMessage(message)
        runCatching { runRecorder.recordRobotRx(message) }
        val type = message.optString("type")
        val now = SystemClock.elapsedRealtime()
        when (type) {
            "hello" -> {
                val armed = message.optBoolean("policyArmed", false)
                val voltage = message.servoBatteryVoltage()
                if (armed) runCatching { transport?.write(RobotProtocol.command("policy_disarm")) }
                handshakeJob?.cancel()
                policyController.onLinkReady()
                mutableStatus.value = RobotStatus(
                    linkState = LinkState.READY,
                    detail = "ESP32 ready",
                    deviceName = mutableStatus.value.deviceName,
                    firmwareVersion = message.optString("version").ifBlank { null },
                    policyArmed = false,
                    servoBatteryVoltage = voltage,
                    servoBatteryLive = message.servoBatteryIsLive(voltage),
                    selectedServoId = selectedServoId,
                    lastMessageAtMs = now,
                )
            }

            "state" -> {
                val voltage = message.servoBatteryVoltage()
                mutableStatus.value = mutableStatus.value.copy(
                    servoBatteryVoltage = voltage,
                    servoBatteryLive = message.servoBatteryIsLive(voltage),
                    lastMessageAtMs = now,
                )
            }

            "policy_state" -> {
                val voltage = message.servoBatteryVoltage()
                val current = mutableStatus.value
                val policyTelemetry = runCatching {
                    ServoTelemetry.fromPolicyState(message, now)
                }.getOrDefault(emptyMap())
                val telemetryById = if (policyTelemetry.isEmpty()) {
                    current.servoTelemetryById
                } else {
                    current.servoTelemetryById + policyTelemetry
                }
                val selectedTelemetry = selectedServoId?.let(telemetryById::get)
                mutableStatus.value = current.copy(
                    detail = "Policy telemetry active",
                    policyArmed = message.optBoolean("armed", false),
                    lastSequence = message.optLong("seq"),
                    feedbackComplete = message.optBoolean("feedback_complete", false),
                    servoBatteryVoltage = selectedTelemetry?.voltage ?: voltage ?: current.servoBatteryVoltage,
                    servoBatteryLive = message.servoBatteryIsLive(voltage),
                    selectedServoId = selectedServoId,
                    servoTelemetry = selectedTelemetry,
                    servoTelemetryById = telemetryById,
                    lastMessageAtMs = now,
                )
            }

            "servo_telemetry" -> {
                val telemetry = runCatching { ServoTelemetry.fromJson(message, now) }.getOrNull()
                if (telemetry != null) {
                    val current = mutableStatus.value
                    val telemetryById = current.servoTelemetryById + (telemetry.id to telemetry)
                    mutableStatus.value = current.copy(
                        servoBatteryVoltage = telemetry.voltage,
                        servoBatteryLive = true,
                        selectedServoId = selectedServoId,
                        servoTelemetry = selectedServoId?.let(telemetryById::get),
                        servoTelemetryById = telemetryById,
                        lastMessageAtMs = now,
                    )
                }
            }

            "policy_disarmed" -> mutableStatus.value = mutableStatus.value.copy(
                detail = "Policy disarmed: ${message.optString("reason", "unknown reason")}",
                policyArmed = false,
                lastMessageAtMs = now,
            )

            "error" -> mutableStatus.value = mutableStatus.value.copy(
                detail = "ESP32: ${message.optString("message", "error")}",
                lastMessageAtMs = now,
            )

            else -> mutableStatus.value = mutableStatus.value.copy(lastMessageAtMs = now)
        }
        refreshForegroundNotification()
    }

    private fun onUsbFailure(error: Throwable) {
        disconnect("USB link lost: ${error.message}")
        scheduleRetry()
    }

    private fun JSONObject.servoBatteryVoltage(): Float? =
        optDouble("servoBatteryVoltage", Double.NaN)
            .takeIf(Double::isFinite)
            ?.toFloat()

    private fun JSONObject.servoBatteryIsLive(voltage: Float?): Boolean =
        if (has("servoBatteryLive")) optBoolean("servoBatteryLive", false) else voltage != null

    private fun findRobotDevice(): UsbDevice? {
        val devices = usbManager.deviceList.values
        return devices.firstOrNull {
            it.vendorId == UsbRobotTransport.ROBOT_VENDOR_ID &&
                it.productId == UsbRobotTransport.ROBOT_PRODUCT_ID
        } ?: devices.firstOrNull { UsbSerialProberCompat.supports(it) }
    }

    private fun scheduleRetry() {
        if (retryJob?.isActive == true) return
        retryJob = scope.launch {
            delay(RETRY_MS)
            if (isActive && transport == null) discoverRobot()
        }
    }

    private fun disconnect(reason: String) {
        policyController.stop(reason)
        closeTransport()
        setStatus(LinkState.ERROR, reason)
    }

    private fun closeTransport() {
        handshakeJob?.cancel()
        handshakeJob = null
        transport?.close()
        transport = null
        connectedDeviceId = null
        decoder.reset()
        if (isForeground && foregroundConnectedDevice) startInForeground(connectedDevice = false)
    }

    private fun setStatus(state: LinkState, detail: String, deviceName: String? = mutableStatus.value.deviceName) {
        mutableStatus.value = RobotStatus(
            linkState = state,
            detail = detail,
            deviceName = deviceName,
            selectedServoId = selectedServoId,
        )
        refreshForegroundNotification()
    }

    private fun createNotificationChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Robot connection", NotificationManager.IMPORTANCE_LOW),
        )
    }

    private fun startInForeground(connectedDevice: Boolean) {
        if (isForeground && foregroundConnectedDevice == connectedDevice) {
            refreshForegroundNotification()
            return
        }
        val contentText = foregroundText(connectedDevice)
        val notification = buildForegroundNotification(contentText)
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification,
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE,
        )
        isForeground = true
        foregroundConnectedDevice = connectedDevice
        lastForegroundText = contentText
    }

    private fun foregroundText(connectedDevice: Boolean = foregroundConnectedDevice): String {
        val robot = mutableStatus.value
        val battery = ServoBatterySafety.evaluate(robot.servoBatteryVoltage, robot.servoBatteryLive)
        return if (battery.isLow) {
            battery.message()
        } else if (connectedDevice) {
            "Watching the ESP32 safety link"
        } else {
            "Waiting for ESP32 USB"
        }
    }

    private fun refreshForegroundNotification() {
        if (!isForeground) return
        val contentText = foregroundText()
        if (contentText == lastForegroundText) return
        getSystemService(NotificationManager::class.java).notify(
            NOTIFICATION_ID,
            buildForegroundNotification(contentText),
        )
        lastForegroundText = contentText
    }

    private fun buildForegroundNotification(contentText: String) =
        NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_robot_notification)
            .setContentTitle("Pixel Robot")
            .setContentText(contentText)
            .setStyle(NotificationCompat.BigTextStyle().bigText(contentText))
            .setContentIntent(activityPendingIntent())
            .addAction(0, "STOP + TORQUE OFF", stopPendingIntent())
            .setOngoing(true)
            .build()

    private fun activityPendingIntent(): PendingIntent =
        PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

    private fun stopPendingIntent(): PendingIntent =
        PendingIntent.getService(
            this,
            1,
            Intent(this, RobotService::class.java).setAction(ACTION_STOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )

    override fun onDestroy() {
        controlServer?.close()
        controlServer = null
        policyController.close()
        runRecorder.close()
        closeTransport()
        if (isForeground) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            isForeground = false
            foregroundConnectedDevice = false
        }
        unregisterReceiver(usbReceiver)
        scope.cancel()
        super.onDestroy()
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        emergencyStop()
        super.onTaskRemoved(rootIntent)
    }

    @Suppress("DEPRECATION")
    private fun Intent.usbDevice(): UsbDevice? =
        if (Build.VERSION.SDK_INT >= 33) getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
        else getParcelableExtra(UsbManager.EXTRA_DEVICE)

    companion object {
        private const val TAG = "RobotService"
        private const val ACTION_USB_PERMISSION = "com.leo.pixelrobot.USB_PERMISSION"
        const val ACTION_RECONNECT = "com.leo.pixelrobot.RECONNECT"
        const val ACTION_STOP = "com.leo.pixelrobot.STOP"
        private const val CHANNEL_ID = "robot_connection"
        private const val NOTIFICATION_ID = 10
        private const val HANDSHAKE_TIMEOUT_MS = 5_000L
        private const val RETRY_MS = 1_000L
        private const val SERVO_TELEMETRY_INTERVAL_MS = 250L
        private const val SERVO_TELEMETRY_LIVE_MS = 1_000L
    }
}

private object UsbSerialProberCompat {
    fun supports(device: UsbDevice): Boolean =
        com.hoho.android.usbserial.driver.UsbSerialProber.getDefaultProber().probeDevice(device) != null
}
