package com.leo.pixelrobot

import android.Manifest
import android.content.ClipData
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.hardware.camera2.CameraCharacteristics
import android.hardware.camera2.CameraManager
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.os.PowerManager
import android.view.View
import android.widget.AdapterView
import android.widget.ArrayAdapter
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.camera.camera2.interop.Camera2CameraInfo
import androidx.camera.camera2.interop.Camera2Interop
import androidx.camera.camera2.interop.ExperimentalCamera2Interop
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.leo.pixelrobot.databinding.ActivityMainBinding
import com.leo.pixelrobot.robot.FirmwareCapabilities
import com.leo.pixelrobot.robot.LinkState
import com.leo.pixelrobot.robot.RobotService
import com.leo.pixelrobot.robot.RobotStatus
import com.leo.pixelrobot.robot.ServoTelemetry
import com.leo.pixelrobot.robot.ServoBatterySafety
import kotlinx.coroutines.Job
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong
import java.util.Locale

class MainActivity : AppCompatActivity() {
    private lateinit var binding: ActivityMainBinding
    private lateinit var cameraExecutor: ExecutorService
    private var cameraProvider: ProcessCameraProvider? = null
    private var robotService: RobotService? = null
    private var statusJob: Job? = null
    private var policyStatusJob: Job? = null
    private val analyzedFrames = AtomicLong()
    private var cameraStartedAtNs = 0L

    private val cameraPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (granted && binding.cameraSwitch.isChecked) {
            startCamera()
        } else if (!granted) {
            binding.cameraSwitch.isChecked = false
            binding.cameraStatus.setText(R.string.camera_permission_denied)
        }
    }

    private val notificationPermission =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            robotService = (binder as RobotService.LocalBinder).service
            configureMotionControls()
            selectDisplayedServo()
            statusJob?.cancel()
            statusJob = lifecycleScope.launch {
                repeatOnLifecycle(Lifecycle.State.STARTED) {
                    robotService?.status?.collect(::renderStatus)
                }
            }
            policyStatusJob?.cancel()
            policyStatusJob = lifecycleScope.launch {
                repeatOnLifecycle(Lifecycle.State.STARTED) {
                    robotService?.policyStatus?.collect { policy ->
                        binding.runtimeStatus.text = buildString {
                            append(policy.detail)
                            policy.feedbackHertz?.let { append(" | %.1f Hz".format(it)) }
                            policy.executionProvider?.let { append(" • $it") }
                            policy.inferenceMilliseconds?.let { append(" • %.2f ms".format(it)) }
                        }
                        robotService?.status?.value?.let(::renderStatus)
                        updateArmAvailability()
                    }
                }
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            robotService = null
            binding.connectionStatus.setText(R.string.robot_service_disconnected)
            updateArmAvailability()
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        ViewCompat.setOnApplyWindowInsetsListener(binding.root) { view, insets ->
            val systemBars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.displayCutout(),
            )
            view.setPadding(systemBars.left, systemBars.top, systemBars.right, systemBars.bottom)
            insets
        }
        cameraExecutor = Executors.newSingleThreadExecutor()

        if (
            Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        }

        ContextCompat.startForegroundService(this, Intent(this, RobotService::class.java))
        binding.reconnectButton.setOnClickListener { robotService?.reconnect() }
        binding.shareLastRunButton.setOnClickListener { shareLastRun() }
        binding.shareTrainingCaptureButton.setOnClickListener { shareTrainingCapture() }
        binding.recordButton.setOnClickListener {
            runCatching {
                val service = requireNotNull(robotService) { "Robot service is not connected" }
                if (service.recordingStatus().active) service.stopRecording() else service.startRecording()
                renderStatus(service.status.value)
            }.onFailure { binding.runtimeStatus.text = it.message }
        }
        binding.stopButton.setOnClickListener {
            binding.yawSlider.value = 0f
            robotService?.emergencyStop()
        }
        binding.startPolicyButton.setOnClickListener {
            runCatching {
                robotService?.updateMotionRequest(binding.forwardSlider.value, binding.yawSlider.value)
                robotService?.startPolicy()
            }.onFailure { binding.runtimeStatus.text = it.message }
        }
        binding.standAtStartPoseButton.setOnClickListener {
            runCatching { robotService?.standAtCapturedPose() }
                .onFailure { binding.runtimeStatus.text = it.message }
        }
        binding.forwardSlider.addOnChangeListener { _, value, _ ->
            runCatching { robotService?.updateMotionRequest(value, binding.yawSlider.value) }
                .onFailure { binding.runtimeStatus.text = it.message }
            renderMotionLabels()
        }
        binding.yawSlider.addOnChangeListener { _, value, _ ->
            runCatching { robotService?.updateMotionRequest(binding.forwardSlider.value, value) }
                .onFailure { binding.runtimeStatus.text = it.message }
            renderMotionLabels()
        }
        binding.cameraSwitch.setOnCheckedChangeListener { _, enabled ->
            if (enabled) enableCamera() else disableCamera()
        }
        configureServoTelemetrySelector()
        renderMotionLabels()
    }

    override fun onStart() {
        super.onStart()
        bindService(Intent(this, RobotService::class.java), serviceConnection, Context.BIND_AUTO_CREATE)
    }

    override fun onStop() {
        statusJob?.cancel()
        statusJob = null
        policyStatusJob?.cancel()
        policyStatusJob = null
        if (robotService != null) unbindService(serviceConnection)
        robotService = null
        super.onStop()
    }

    override fun onDestroy() {
        cameraExecutor.shutdown()
        super.onDestroy()
    }

    private fun renderStatus(status: RobotStatus) {
        val servoBattery = ServoBatterySafety.evaluate(
            status.servoBatteryVoltage,
            status.servoBatteryLive,
        )
        binding.connectionStatus.text = when (status.linkState) {
            LinkState.READY -> buildString {
                append("USB ready${status.firmwareVersion?.let { " • firmware $it" } ?: ""}")
                if (servoBattery.isLow) append("\n${servoBattery.message()}")
            }
            else -> status.detail
        }
        binding.connectionStatus.setTextColor(
            ContextCompat.getColor(this, if (servoBattery.isLow) R.color.danger else R.color.ink),
        )
        binding.telemetryStatus.text = buildString {
            append("device: ${status.deviceName ?: "none"}")
            status.lastSequence?.let { append("\nsequence: $it") }
            append("\n${servoBattery.message()}")
            status.feedbackComplete?.let { append(" • feedback: ${if (it) "complete" else "INCOMPLETE"}") }
        }
        robotService?.recordingStatus()?.let { recording ->
            val summary = if (recording.active) {
                "recording: ${recording.activeFileName} • ${recording.recordCount} records"
            } else {
                "last run: ${recording.latestFileName ?: "none"}"
            }
            binding.telemetryStatus.append("\n$summary")
            recording.error?.let { binding.telemetryStatus.append("\nrecording error: $it") }
        }
        binding.shareLastRunButton.isEnabled = robotService?.latestRunFile() != null
        binding.shareTrainingCaptureButton.isEnabled = robotService?.latestRunFile() != null
        binding.recordButton.text = getString(
            if (robotService?.recordingStatus()?.active == true) R.string.stop_recording else R.string.start_recording,
        )
        renderServoTelemetry(status)
        updateArmAvailability()
    }

    private fun configureServoTelemetrySelector() {
        val choices = listOf(getString(R.string.select_servo_id)) + (1..12).map { "Servo ID $it" }
        binding.servoIdSpinner.adapter = ArrayAdapter(
            this,
            android.R.layout.simple_spinner_item,
            choices,
        ).also { it.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item) }
        binding.servoIdSpinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                selectDisplayedServo()
            }

            override fun onNothingSelected(parent: AdapterView<*>?) {
                robotService?.selectServoTelemetry(null)
            }
        }
    }

    private fun selectDisplayedServo() {
        val position = binding.servoIdSpinner.selectedItemPosition
        robotService?.selectServoTelemetry(position.takeIf { it in 1..12 })
    }

    private fun renderServoTelemetry(status: RobotStatus) {
        val selectedId = status.selectedServoId
        val spinnerPosition = selectedId ?: 0
        if (binding.servoIdSpinner.selectedItemPosition != spinnerPosition) {
            binding.servoIdSpinner.setSelection(spinnerPosition)
        }
        val policy = robotService?.policyStatus?.value
        val telemetry = status.servoTelemetry?.takeIf { it.id == selectedId }
        binding.servoTorqueValue.text = if (telemetry == null) {
            getString(R.string.servo_torque_waiting)
        } else {
            String.format(Locale.US, "Torque: %+.3f N m", telemetry.estimatedTorqueNm)
        }
        binding.servoForceStatus.text = when {
            selectedId == null -> getString(R.string.servo_load_waiting)
            status.linkState != LinkState.READY -> "Connect the ESP32 to read servo $selectedId."
            !FirmwareCapabilities.supportsPolicyServoTelemetry(status.firmwareVersion) ->
                "ESP32 firmware ${status.firmwareVersion ?: "unknown"} cannot stream torque. Install firmware 0.1.12."
            telemetry != null && policy?.active == true -> telemetry.formatPolicyTorqueForDisplay()
            telemetry != null -> telemetry.formatForDisplay()
            policy?.active == true -> "Waiting for the next policy-frame torque sample from servo $selectedId..."
            policy?.holdingPose != true -> "Servo $selectedId selected. Tap STAND or start the policy to read load."
            else -> "Waiting for servo $selectedId telemetry..."
        }
    }

    private fun ServoTelemetry.formatForDisplay(): String = String.format(
        Locale.US,
        "%s, ID %d\nLoad: %+.1f%% (raw %d)\nCurrent: %+.0f mA (raw %d)\nEstimated joint torque: %+.3f N m, %+.2f kg cm\nPosition: %.2f deg joint, %.2f deg raw (%d)\nSpeed: %+.3f rpm (raw %d), moving: %s\nVoltage: %.1f V, temperature: %d C\nStatus: servo 0x%02X, packet 0x%02X, async %d",
        name,
        id,
        loadPercent,
        loadRaw,
        currentMilliamps,
        currentRaw,
        estimatedTorqueNm,
        estimatedTorqueKgCm,
        jointAngleDegrees,
        positionDegrees,
        positionRaw,
        speedRpm,
        speedRaw,
        if (moving) "yes" else "no",
        voltage,
        temperatureCelsius,
        servoStatus,
        packetStatus,
        asyncWriteFlag,
    )

    private fun ServoTelemetry.formatPolicyTorqueForDisplay(): String = String.format(
        Locale.US,
        "All 12 servo currents sampled every policy frame\n%s, ID %d\nCurrent: %+.0f mA (raw %d)\nEstimated joint torque: %+.3f N m, %+.2f kg cm\nJoint position: %.2f deg",
        name,
        id,
        currentMilliamps,
        currentRaw,
        estimatedTorqueNm,
        estimatedTorqueKgCm,
        jointAngleDegrees,
    )

    private fun updateArmAvailability() {
        val ready = robotService?.status?.value?.linkState == LinkState.READY
        val clockedPolicyReady = FirmwareCapabilities.supportsClockedPolicyFeedback(
            robotService?.status?.value?.firmwareVersion,
        )
        val idle = robotService?.policyStatus?.value?.active != true
        binding.startPolicyButton.isEnabled = ready && clockedPolicyReady && idle
        binding.standAtStartPoseButton.isEnabled =
            ready && idle && robotService?.policyStatus?.value?.holdingPose != true
    }

    private fun configureMotionControls() {
        val service = robotService ?: return
        binding.forwardSlider.valueFrom = service.forwardMinimum
        binding.forwardSlider.valueTo = service.forwardMaximum
        binding.forwardSlider.value = binding.forwardSlider.value.coerceIn(
            service.forwardMinimum,
            service.forwardMaximum,
        )
        binding.yawSlider.valueFrom = service.yawMinimum
        binding.yawSlider.valueTo = service.yawMaximum
        binding.yawSlider.value = binding.yawSlider.value.coerceIn(service.yawMinimum, service.yawMaximum)
        renderMotionLabels()
    }

    private fun renderMotionLabels() {
        binding.forwardRequestLabel.text = getString(R.string.forward_request, binding.forwardSlider.value)
        binding.yawRequestLabel.text = getString(R.string.yaw_request, binding.yawSlider.value)
    }

    private fun shareLastRun() {
        val file = robotService?.latestRunFile()
        if (file == null) {
            binding.runtimeStatus.setText(R.string.no_completed_run)
            return
        }
        val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
        val share = Intent(Intent.ACTION_SEND)
            .setType("application/x-ndjson")
            .putExtra(Intent.EXTRA_STREAM, uri)
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        share.clipData = ClipData.newRawUri(file.name, uri)
        startActivity(Intent.createChooser(share, getString(R.string.share_run_chooser)))
    }

    private fun shareTrainingCapture() {
        binding.shareTrainingCaptureButton.isEnabled = false
        binding.runtimeStatus.text = "Building verified training capture..."
        lifecycleScope.launch {
            val result = runCatching {
                withContext(Dispatchers.IO) { robotService?.latestTrainingCaptureFile() }
            }
            binding.shareTrainingCaptureButton.isEnabled = robotService?.latestRunFile() != null
            val file = result.onFailure { binding.runtimeStatus.text = it.message }.getOrNull()
            if (file == null) {
                if (result.isSuccess) binding.runtimeStatus.setText(R.string.no_completed_run)
                return@launch
            }
            val uri = FileProvider.getUriForFile(this@MainActivity, "$packageName.files", file)
            val share = Intent(Intent.ACTION_SEND)
                .setType("application/zip")
                .putExtra(Intent.EXTRA_STREAM, uri)
                .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            share.clipData = ClipData.newRawUri(file.name, uri)
            startActivity(
                Intent.createChooser(share, getString(R.string.share_training_capture_chooser)),
            )
        }
    }

    @androidx.annotation.OptIn(markerClass = [ExperimentalCamera2Interop::class])
    private fun startCamera() {
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            if (!binding.cameraSwitch.isChecked) return@addListener
            runCatching {
                val provider = providerFuture.get()
                cameraProvider = provider
                val lens = requireNotNull(findRearUltraWideLens()) {
                    "The rear 0.5 camera is unavailable"
                }
                val selector = CameraSelector.Builder()
                    .addCameraFilter { cameraInfos ->
                        cameraInfos.filter { Camera2CameraInfo.from(it).cameraId == lens.logicalCameraId }
                    }
                    .build()
                val previewBuilder = Preview.Builder()
                Camera2Interop.Extender(previewBuilder).setPhysicalCameraId(lens.physicalCameraId)
                val preview = previewBuilder.build().also {
                    it.surfaceProvider = binding.cameraPreview.surfaceProvider
                }
                val analysisBuilder = ImageAnalysis.Builder()
                    .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                Camera2Interop.Extender(analysisBuilder).setPhysicalCameraId(lens.physicalCameraId)
                val analysis = analysisBuilder.build()
                cameraStartedAtNs = System.nanoTime()
                analyzedFrames.set(0)
                analysis.setAnalyzer(cameraExecutor) { image ->
                    val count = analyzedFrames.incrementAndGet()
                    image.close()
                    if (count % 30L == 0L) {
                        val seconds = (System.nanoTime() - cameraStartedAtNs) / 1_000_000_000.0
                        val fps = count / seconds.coerceAtLeast(0.001)
                        val thermal = getSystemService(PowerManager::class.java).currentThermalStatus
                        runOnUiThread {
                            binding.cameraStatus.text = getString(
                                R.string.camera_analysis_status,
                                fps,
                                thermalLabel(thermal),
                            )
                        }
                    }
                }
                provider.unbindAll()
                provider.bindToLifecycle(this, selector, preview, analysis)
            }.onFailure { error ->
                cameraProvider?.unbindAll()
                cameraProvider = null
                binding.cameraSwitch.isChecked = false
                binding.cameraStatus.text = getString(
                    R.string.camera_unavailable,
                    error.message ?: "unknown error",
                )
            }
        }, ContextCompat.getMainExecutor(this))
    }

    private fun findRearUltraWideLens(): UltraWideLens? {
        val manager = getSystemService(CameraManager::class.java)
        return manager.cameraIdList.asSequence()
            .mapNotNull logicalCamera@{ logicalCameraId ->
                val logical = manager.getCameraCharacteristics(logicalCameraId)
                if (logical[CameraCharacteristics.LENS_FACING] != CameraCharacteristics.LENS_FACING_BACK) {
                    return@logicalCamera null
                }
                logical.physicalCameraIds.mapNotNull physicalCamera@{ physicalCameraId ->
                    val focalLength = manager.getCameraCharacteristics(physicalCameraId)
                        .get(CameraCharacteristics.LENS_INFO_AVAILABLE_FOCAL_LENGTHS)
                        ?.minOrNull()
                        ?: return@physicalCamera null
                    UltraWideLens(logicalCameraId, physicalCameraId, focalLength)
                }.minByOrNull(UltraWideLens::focalLengthMm)
            }
            .minByOrNull(UltraWideLens::focalLengthMm)
    }

    private fun enableCamera() {
        binding.cameraPreview.visibility = View.VISIBLE
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
            startCamera()
        } else {
            cameraPermission.launch(Manifest.permission.CAMERA)
        }
    }

    private fun disableCamera() {
        cameraProvider?.unbindAll()
        cameraProvider = null
        analyzedFrames.set(0)
        binding.cameraPreview.visibility = View.GONE
        binding.cameraStatus.setText(R.string.camera_off)
    }

    private fun thermalLabel(value: Int): String = when (value) {
        PowerManager.THERMAL_STATUS_NONE -> "none"
        PowerManager.THERMAL_STATUS_LIGHT -> "light"
        PowerManager.THERMAL_STATUS_MODERATE -> "moderate"
        PowerManager.THERMAL_STATUS_SEVERE -> "severe"
        PowerManager.THERMAL_STATUS_CRITICAL -> "critical"
        PowerManager.THERMAL_STATUS_EMERGENCY -> "emergency"
        PowerManager.THERMAL_STATUS_SHUTDOWN -> "shutdown"
        else -> "unknown"
    }

    private data class UltraWideLens(
        val logicalCameraId: String,
        val physicalCameraId: String,
        val focalLengthMm: Float,
    )
}
