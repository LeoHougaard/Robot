package com.leo.pixelrobot.robot

import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbDeviceConnection
import android.hardware.usb.UsbManager
import android.os.SystemClock
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialProber
import org.json.JSONObject
import java.io.Closeable
import java.util.concurrent.CompletableFuture
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/** USB lifecycle adapter. All outgoing bytes go through the application-owned bounded writer. */
class UsbRobotTransport(
    private val manager: UsbManager,
    private val device: UsbDevice,
    private val onBytes: (ByteArray) -> Unit,
    private val onFailure: (Throwable) -> Unit,
    private val onWireBytes: ((direction: String, bytes: ByteArray, monotonicNs: Long) -> Unit)? = null,
) : Closeable {
    private val lifecycleLock = Any()
    private var opened = false
    @Volatile private var closing = false
    @Volatile private var writer: BoundedUsbWriter? = null
    private var teardown: UsbTeardown? = null
    private val callbacksEnabled = AtomicBoolean(false)
    private val failureReported = AtomicBoolean(false)
    private val openCleanup = CompletableFuture<Unit>()
    private val startup = UsbStartupFence()

    fun open() {
        synchronized(lifecycleLock) {
            check(!opened && !closing) { "USB transport cannot be reopened" }
            opened = true
        }
        val writeExecutor = Executors.newSingleThreadExecutor { runnable ->
            Thread(runnable, "robot-usb-writer").apply { isDaemon = true }
        }
        val closeExecutor = Executors.newSingleThreadExecutor { runnable ->
            Thread(runnable, "robot-usb-close").apply { isDaemon = true }
        }
        val timerExecutor = Executors.newSingleThreadScheduledExecutor { runnable ->
            Thread(runnable, "robot-usb-deadline").apply { isDaemon = true }
        }
        var reader: Thread? = null
        val readerStopped = CompletableFuture<Unit>()
        val reading = AtomicBoolean(false)
        var allocatedPort: UsbSerialPort? = null
        var allocatedConnection: UsbDeviceConnection? = null
        try {
            val driver = UsbSerialProber.getDefaultProber().probeDevice(device)
                ?: error("No serial driver for ${device.vendorId.toString(16)}:${device.productId.toString(16)}")
            val connection = (manager.openDevice(device) ?: error("Android USB permission was not granted"))
                .also { allocatedConnection = it }
            val openedPort = driver.ports.first().also { allocatedPort = it }
            openedPort.open(connection)
            openedPort.setParameters(BAUD, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE)
            runCatching { openedPort.dtr = false }
            runCatching { openedPort.rts = false }
            val bounded = BoundedUsbWriter(
                sink = object : UsbWriteSink {
                    override fun write(bytes: ByteArray, timeoutMs: Int) {
                        val framed = startup.frameForNativeWrite(bytes)
                        observeWire("tx", framed)
                        openedPort.write(framed, timeoutMs)
                    }
                    override fun close() {
                        callbacksEnabled.set(false)
                        reading.set(false)
                        try { openedPort.close() } finally { connection.close() }
                        if (reader != null) readerStopped.get()
                    }
                },
                writerExecutor = writeExecutor,
                closeExecutor = closeExecutor,
                deadlines = ExecutorUsbDeadlineScheduler(timerExecutor),
                safetyBytes = RobotProtocol.command("stop") + RobotProtocol.command("policy_disarm") +
                    RobotProtocol.torqueAll(false),
                onFailure = { error ->
                    callbacksEnabled.set(false)
                    if (!closing && failureReported.compareAndSet(false, true)) onFailure(error)
                },
            )
            releaseExecutors = {
                writeExecutor.shutdown()
                closeExecutor.shutdown()
                timerExecutor.shutdown()
            }
            synchronized(lifecycleLock) {
                writer = bounded
                if (closing) {
                    bounded.fail(IllegalStateException("USB closed during capability handshake"))
                    val finishedOpen = bounded.closeAsync()
                    val waiting = teardown
                    if (waiting == null) teardown = finishedOpen else {
                        finishedOpen.outcome.whenComplete { report, error ->
                            if (error == null) waiting.outcome.complete(report) else waiting.outcome.completeExceptionally(error)
                        }
                        finishedOpen.quiesced.whenComplete { _, error ->
                            if (error == null) waiting.quiesced.complete(Unit) else waiting.quiesced.completeExceptionally(error)
                        }
                    }
                } else {
                    callbacksEnabled.set(true)
                    openedPort.setReadQueue(256, openedPort.readEndpoint.maxPacketSize)
                    reading.set(true)
                    reader = Thread({
                        val buffer = ByteArray(openedPort.readEndpoint.maxPacketSize)
                        try {
                            while (reading.get()) {
                                val count = openedPort.read(buffer, 100)
                                if (count > 0) {
                                    val data = buffer.copyOf(count)
                                    observeWire("rx", data)
                                    if (callbacksEnabled.get()) {
                                        if (startup.isReady()) onBytes(data) else startup.acceptBeforeReady(data)
                                    }
                                }
                            }
                        } catch (error: Throwable) {
                            if (callbacksEnabled.get()) bounded.fail(error)
                        } finally { readerStopped.complete(Unit) }
                    }, "pixel-usb-reader").apply { isDaemon = true; start() }
                    bounded.write(startup.challenge())
                }
            }
            // The service invokes open on its I/O scope. An old firmware link receives
            // only CAN plus a random fixed-length nonce and is closed, without LF or
            // torque-off records, if no exact ACK arrives. No ordinary operation is admitted while capability is unknown.
            startup.acknowledged.get(STARTUP_ACK_TIMEOUT_MS, TimeUnit.MILLISECONDS)
            check(!closing) { "USB closed during capability handshake" }
            // A close during open also releases these per-link executor resources.
            teardown?.quiesced?.whenComplete { _, _ ->
                writeExecutor.shutdown()
                closeExecutor.shutdown()
                timerExecutor.shutdown()
            }
        } catch (error: Throwable) {
            callbacksEnabled.set(false)
            val active = writer
            if (active != null) {
                // Native writer/close ownership remains with the arbiter, including errors
                // during handshake. Never start a competing synchronous close here.
                startup.permitSafetyOrAbort(error)
                closing = true
                active.fail(error)
                val result = closeAsync()
                result.quiesced.whenComplete { _, closeError ->
                    if (closeError == null) openCleanup.complete(Unit) else openCleanup.completeExceptionally(closeError)
                }
            } else {
                reading.set(false)
                val portClose = runCatching { allocatedPort?.close() }
                val connectionClose = runCatching { allocatedConnection?.close() }
                val cleanupError = portClose.exceptionOrNull() ?: connectionClose.exceptionOrNull()
                if (cleanupError == null) openCleanup.complete(Unit) else openCleanup.completeExceptionally(cleanupError)
                writeExecutor.shutdown()
                closeExecutor.shutdown()
                timerExecutor.shutdown()
            }
            throw error
        }
    }

    @Volatile private var releaseExecutors: () -> Unit = {}

    fun currentGeneration(): UsbWriteGeneration = requireWriter().currentGeneration()

    fun advanceGeneration(): UsbWriteGeneration = requireWriter().advanceGeneration()

    fun write(
        bytes: ByteArray,
        generation: UsbWriteGeneration = currentGeneration(),
        withAuthorization: ((() -> Unit) -> Unit) = { it() },
    ) = requireWriter().write(bytes, generation, withAuthorization)

    fun requestSafetyStop(): CompletableFuture<UsbSafetyWriteReport> {
        val active = checkNotNull(writer) { "USB serial is not open" }
        if (!startup.permitSafetyOrAbort(IllegalStateException("USB stopped before capability ACK"))) {
            active.fail(IllegalStateException("USB firmware abort capability is unverified; no newline sent"))
        }
        return active.requestSafetyStop()
    }

    /** Starts bounded background safety/close work; never blocks Android's main thread. */
    fun closeAsync(): UsbTeardown = synchronized(lifecycleLock) {
        teardown?.let { return it }
        closing = true
        callbacksEnabled.set(false)
        val safetyPermitted = startup.permitSafetyOrAbort(IllegalStateException("USB closed before capability ACK"))
        val active = writer
        val result = if (active != null) {
            if (!safetyPermitted) {
                active.fail(IllegalStateException("USB firmware abort capability is unverified; no newline sent"))
            }
            active.closeAsync()
        } else {
            // A close racing open cannot grant reconnect permission until that native
            // open returns and its resulting port is closed. The bounded outcome says only
            // delivery unknown; it does not pretend an in-progress open was cancelled.
            UsbTeardown(
                CompletableFuture.completedFuture(UsbSafetyWriteReport(UsbSafetyWriteOutcome.WRITE_FAILED, false, 0)),
                if (opened) openCleanup else CompletableFuture.completedFuture(Unit),
            )
        }
        teardown = result
        result.quiesced.whenComplete { _, _ -> releaseExecutors() }
        result
    }

    override fun close() { closeAsync() }

    private fun requireWriter(): BoundedUsbWriter {
        check(startup.isReady() && !closing) { "USB firmware abort capability is unverified or link is closing" }
        return checkNotNull(writer) { "USB serial is not open" }
    }

    private fun observeWire(direction: String, bytes: ByteArray) {
        val observer = onWireBytes ?: return
        runCatching { observer(direction, bytes.copyOf(), SystemClock.elapsedRealtimeNanos()) }
    }

    private fun safetyRecords() = listOf(RobotProtocol.command("stop"),
        RobotProtocol.command("policy_disarm"), RobotProtocol.torqueAll(false))

    companion object {
        const val ROBOT_VENDOR_ID = 0x10C4
        const val ROBOT_PRODUCT_ID = 0xEA60
        const val BAUD = 2_000_000
        private const val STARTUP_ACK_TIMEOUT_MS = 750L
    }
}
