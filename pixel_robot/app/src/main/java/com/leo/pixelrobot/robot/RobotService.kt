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
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import android.util.Log
import android.util.AtomicFile
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
    private lateinit var calibrationOverrideFile: File
    private var controlServer: RobotControlServer? = null
    private var transport: UsbRobotTransport? = null
    private var connectedDeviceId: Int? = null
    @Volatile private var openingDeviceId: Int? = null
    private var permissionRequestDeviceId: Int? = null
    private var isForeground = false
    private var handshakeJob: Job? = null
    private var retryJob: Job? = null

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
        usbManager = getSystemService(UsbManager::class.java)
        policyContract = PolicyContract.load(assets)
        val profileId = policyContract.profileId
        calibrationOverrideFile = File(filesDir, "$profileId.calibration.json")
        val calibrationOverride = calibrationOverrideFile
            .takeIf(File::isFile)
            ?.readText(Charsets.UTF_8)
        policyController = PolicyController(
            assets = assets,
            scope = scope,
            calibrationOverrideJson = calibrationOverride,
            saveCalibrationOverride = ::persistCalibrationOverride,
            send = { bytes ->
                transport?.write(bytes) ?: error("ESP32 USB link is not ready")
            },
        )
        controlServer = runCatching {
            RobotControlServer(
                assets = assets,
                status = ::controlStatus,
                updateCommand = ::updateMotionRequest,
                capture = ::captureCurrentStartPose,
                stand = ::standAtCapturedPose,
                startTest = ::startSuspendedPolicyTest,
                stop = ::emergencyStop,
                reconnect = ::reconnect,
            ).also(RobotControlServer::start)
        }.onFailure { Log.e(TAG, "Could not start loopback control bridge", it) }.getOrNull()
        createNotificationChannel()
        val receiverFilter = IntentFilter().apply {
            addAction(ACTION_USB_PERMISSION)
            addAction(UsbManager.ACTION_USB_DEVICE_ATTACHED)
            addAction(UsbManager.ACTION_USB_DEVICE_DETACHED)
        }
        ContextCompat.registerReceiver(this, usbReceiver, receiverFilter, ContextCompat.RECEIVER_NOT_EXPORTED)
        discoverRobot()
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_RECONNECT) reconnect()
        return START_STICKY
    }

    fun reconnect() {
        retryJob?.cancel()
        permissionRequestDeviceId = null
        if (policyController.status.value.active) policyController.stop("USB reconnect requested")
        closeTransport()
        discoverRobot()
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

    fun updateMotionRequest(forward: Float, yawRate: Float) {
        policyController.updateRequest(forward, yawRate)
    }

    fun startSuspendedPolicyTest() {
        check(mutableStatus.value.linkState == LinkState.READY) { "ESP32 is not ready" }
        policyController.startSuspendedTest()
    }

    fun standAtCapturedPose() {
        check(mutableStatus.value.linkState == LinkState.READY) { "ESP32 is not ready" }
        policyController.standAtCapturedPose()
    }

    fun captureCurrentStartPose() {
        check(mutableStatus.value.linkState == LinkState.READY) { "ESP32 is not ready" }
        policyController.captureCurrentStartPose()
    }

    private fun controlStatus(): JSONObject {
        val robot = mutableStatus.value
        val policy = policyController.status.value
        return JSONObject()
            .put("link_state", robot.linkState.name.lowercase())
            .put("robot_detail", robot.detail)
            .put("firmware_version", robot.firmwareVersion)
            .put("policy_armed", robot.policyArmed)
            .put("feedback_complete", robot.feedbackComplete)
            .put("policy_active", policy.active)
            .put("policy_commanding", policy.commanding)
            .put("holding_pose", policy.holdingPose)
            .put("policy_detail", policy.detail)
            .put("sequence", policy.sequence)
            .put("inference_ms", policy.inferenceMilliseconds)
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
            .put("yaw_min", policyContract.yawMinimum)
            .put("yaw_max", policyContract.yawMaximum)
    }

    private fun persistCalibrationOverride(json: String) {
        val atomic = AtomicFile(calibrationOverrideFile)
        val stream = atomic.startWrite()
        try {
            stream.write(json.toByteArray(Charsets.UTF_8))
            atomic.finishWrite(stream)
        } catch (error: Throwable) {
            atomic.failWrite(stream)
            throw error
        }
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
                startInForeground()
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
            while (isActive && mutableStatus.value.linkState == LinkState.HANDSHAKING) {
                if (SystemClock.elapsedRealtime() >= deadline) {
                    disconnect("Timed out waiting for ESP32 hello")
                    scheduleRetry()
                    return@launch
                }
                runCatching { transport?.write(RobotProtocol.command("hello")) }
                    .onFailure {
                        disconnect("USB handshake failed: ${it.message}")
                        scheduleRetry()
                        return@launch
                    }
                delay(500)
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
        val type = message.optString("type")
        val now = SystemClock.elapsedRealtime()
        when (type) {
            "hello" -> {
                val armed = message.optBoolean("policyArmed", false)
                if (armed) runCatching { transport?.write(RobotProtocol.command("policy_disarm")) }
                handshakeJob?.cancel()
                policyController.onLinkReady()
                mutableStatus.value = RobotStatus(
                    linkState = LinkState.READY,
                    detail = "ESP32 ready",
                    deviceName = mutableStatus.value.deviceName,
                    firmwareVersion = message.optString("version").ifBlank { null },
                    policyArmed = false,
                    lastMessageAtMs = now,
                )
            }

            "policy_state" -> mutableStatus.value = mutableStatus.value.copy(
                detail = "Policy telemetry active",
                policyArmed = message.optBoolean("armed", false),
                lastSequence = message.optLong("seq"),
                feedbackComplete = message.optBoolean("feedback_complete", false),
                lastMessageAtMs = now,
            )

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
    }

    private fun onUsbFailure(error: Throwable) {
        disconnect("USB link lost: ${error.message}")
        scheduleRetry()
    }

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
        if (isForeground) {
            stopForeground(STOP_FOREGROUND_REMOVE)
            isForeground = false
        }
    }

    private fun setStatus(state: LinkState, detail: String, deviceName: String? = mutableStatus.value.deviceName) {
        mutableStatus.value = RobotStatus(linkState = state, detail = detail, deviceName = deviceName)
    }

    private fun createNotificationChannel() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, "Robot connection", NotificationManager.IMPORTANCE_LOW),
        )
    }

    private fun startInForeground() {
        if (isForeground) return
        val activityIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_robot_notification)
            .setContentTitle("Pixel Robot")
            .setContentText("Watching the ESP32 safety link")
            .setContentIntent(activityIntent)
            .setOngoing(true)
            .build()
        ServiceCompat.startForeground(
            this,
            NOTIFICATION_ID,
            notification,
            ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE,
        )
        isForeground = true
    }

    override fun onDestroy() {
        controlServer?.close()
        controlServer = null
        policyController.close()
        closeTransport()
        unregisterReceiver(usbReceiver)
        scope.cancel()
        super.onDestroy()
    }

    @Suppress("DEPRECATION")
    private fun Intent.usbDevice(): UsbDevice? =
        if (Build.VERSION.SDK_INT >= 33) getParcelableExtra(UsbManager.EXTRA_DEVICE, UsbDevice::class.java)
        else getParcelableExtra(UsbManager.EXTRA_DEVICE)

    companion object {
        private const val TAG = "RobotService"
        private const val ACTION_USB_PERMISSION = "com.leo.pixelrobot.USB_PERMISSION"
        const val ACTION_RECONNECT = "com.leo.pixelrobot.RECONNECT"
        private const val CHANNEL_ID = "robot_connection"
        private const val NOTIFICATION_ID = 10
        private const val HANDSHAKE_TIMEOUT_MS = 25_000L
        private const val RETRY_MS = 2_000L
    }
}

private object UsbSerialProberCompat {
    fun supports(device: UsbDevice): Boolean =
        com.hoho.android.usbserial.driver.UsbSerialProber.getDefaultProber().probeDevice(device) != null
}
