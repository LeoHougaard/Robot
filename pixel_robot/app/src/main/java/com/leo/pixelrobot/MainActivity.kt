package com.leo.pixelrobot

import android.Manifest
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.os.Bundle
import android.os.IBinder
import android.os.PowerManager
import android.view.View
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.app.AlertDialog
import androidx.camera.core.CameraSelector
import androidx.camera.core.ImageAnalysis
import androidx.camera.core.Preview
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.leo.pixelrobot.databinding.ActivityMainBinding
import com.leo.pixelrobot.robot.LinkState
import com.leo.pixelrobot.robot.RobotService
import com.leo.pixelrobot.robot.RobotStatus
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicLong

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

    private val serviceConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            robotService = (binder as RobotService.LocalBinder).service
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
                            policy.executionProvider?.let { append(" • $it") }
                            policy.inferenceMilliseconds?.let { append(" • %.2f ms".format(it)) }
                        }
                        updateArmAvailability()
                    }
                }
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            robotService = null
            binding.connectionStatus.setText(R.string.robot_service_disconnected)
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)
        cameraExecutor = Executors.newSingleThreadExecutor()

        startService(Intent(this, RobotService::class.java))
        binding.reconnectButton.setOnClickListener { robotService?.reconnect() }
        binding.stopButton.setOnClickListener {
            binding.forwardSlider.value = 0f
            binding.yawSlider.value = 0f
            robotService?.emergencyStop()
        }
        binding.startPolicyButton.setOnClickListener {
            robotService?.updateMotionRequest(binding.forwardSlider.value, binding.yawSlider.value)
            robotService?.startSuspendedPolicyTest()
        }
        binding.captureStartPoseButton.setOnClickListener {
            AlertDialog.Builder(this)
                .setTitle(R.string.capture_start_pose_title)
                .setMessage(R.string.capture_start_pose_warning)
                .setNegativeButton(android.R.string.cancel, null)
                .setPositiveButton(R.string.capture_start_pose_confirm) { _, _ ->
                    binding.startPoseConfirmation.isChecked = false
                    runCatching { robotService?.captureCurrentStartPose() }
                        .onFailure { binding.runtimeStatus.text = it.message }
                }
                .show()
        }
        binding.forwardSlider.addOnChangeListener { _, value, _ ->
            robotService?.updateMotionRequest(value, binding.yawSlider.value)
        }
        binding.yawSlider.addOnChangeListener { _, value, _ ->
            robotService?.updateMotionRequest(binding.forwardSlider.value, value)
        }
        binding.liftedConfirmation.setOnCheckedChangeListener { _, _ -> updateArmAvailability() }
        binding.startPoseConfirmation.setOnCheckedChangeListener { _, _ -> updateArmAvailability() }
        binding.cameraSwitch.setOnCheckedChangeListener { _, enabled ->
            if (enabled) enableCamera() else disableCamera()
        }
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
        binding.connectionStatus.text = when (status.linkState) {
            LinkState.READY -> "USB ready${status.firmwareVersion?.let { " • firmware $it" } ?: ""}"
            else -> status.detail
        }
        binding.telemetryStatus.text = buildString {
            append("device: ${status.deviceName ?: "none"}")
            status.lastSequence?.let { append("\nsequence: $it") }
            status.feedbackComplete?.let { append(" • feedback: ${if (it) "complete" else "INCOMPLETE"}") }
        }
        updateArmAvailability()
    }

    private fun updateArmAvailability() {
        val ready = robotService?.status?.value?.linkState == LinkState.READY
        val idle = robotService?.policyStatus?.value?.active != true
        binding.startPolicyButton.isEnabled =
            ready && idle && binding.liftedConfirmation.isChecked
        binding.captureStartPoseButton.isEnabled =
            ready && idle && binding.startPoseConfirmation.isChecked
    }

    private fun startCamera() {
        val providerFuture = ProcessCameraProvider.getInstance(this)
        providerFuture.addListener({
            if (!binding.cameraSwitch.isChecked) return@addListener
            val provider = providerFuture.get()
            cameraProvider = provider
            val preview = Preview.Builder().build().also {
                it.surfaceProvider = binding.cameraPreview.surfaceProvider
            }
            val analysis = ImageAnalysis.Builder()
                .setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST)
                .build()
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
            provider.bindToLifecycle(this, CameraSelector.DEFAULT_BACK_CAMERA, preview, analysis)
        }, ContextCompat.getMainExecutor(this))
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
}
