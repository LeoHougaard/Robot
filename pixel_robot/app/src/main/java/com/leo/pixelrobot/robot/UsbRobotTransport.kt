package com.leo.pixelrobot.robot

import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialProber
import org.json.JSONObject
import java.io.Closeable

class UsbRobotTransport(
    private val manager: UsbManager,
    private val device: UsbDevice,
    private val onBytes: (ByteArray) -> Unit,
    private val onFailure: (Throwable) -> Unit,
) : Closeable {
    private var port: UsbSerialPort? = null
    private var writer: BoundedSerialWriter? = null
    @Volatile private var reading = false
    private var reader: Thread? = null

    fun open() {
        check(port == null) { "USB transport is already open" }
        val driver = UsbSerialProber.getDefaultProber().probeDevice(device)
            ?: error("No serial driver for ${device.vendorId.toString(16)}:${device.productId.toString(16)}")
        val connection = manager.openDevice(device) ?: error("Android USB permission was not granted")
        try {
            val openedPort = driver.ports.first()
            openedPort.open(connection)
            openedPort.setParameters(BAUD, 8, UsbSerialPort.STOPBITS_1, UsbSerialPort.PARITY_NONE)
            runCatching { openedPort.dtr = false }
            runCatching { openedPort.rts = false }
            port = openedPort
            openedPort.setReadQueue(256, openedPort.readEndpoint.maxPacketSize)
            writer = BoundedSerialWriter(openedPort::write, { reading = false; openedPort.close() }, onFailure)
            reading = true
            reader = Thread({
                val buffer = ByteArray(openedPort.readEndpoint.maxPacketSize)
                try {
                    while (reading) {
                        val count = openedPort.read(buffer, 100)
                        if (reading && count > 0) onBytes(buffer.copyOf(count))
                    }
                } catch (error: Throwable) { if (reading) onFailure(error) }
            }, "pixel-usb-reader").apply {
                isDaemon = true
                start()
            }
        } catch (error: Throwable) {
            connection.close()
            throw error
        }
    }

    @Synchronized
    fun write(bytes: ByteArray, gate: (() -> Unit) -> Unit = { it() }) {
        val active = checkNotNull(writer) { "USB serial is not open" }
        val command = JSONObject(bytes.toString(Charsets.UTF_8))
        when (command.optString("cmd")) {
            "stop" -> active.stop(safetyRecords())
            "policy_arm", "program_start" -> { active.resume(); active.enqueue(bytes, gate = gate) }
            "servo_torque" -> {
                if (command.optBoolean("enabled")) active.resume()
                // stop() already includes torque-off. Repeated safety commands share its barrier.
                if (command.optBoolean("enabled")) active.enqueue(bytes, gate = gate)
                else active.stop(safetyRecords())
            }
            "policy_disarm" -> active.stop(safetyRecords())
            else -> active.enqueue(bytes, allowWhileStopped = command.optString("cmd") !in
                setOf("policy_frame", "play", "servo", "move"), gate = gate)
        }
    }

    @Synchronized
    override fun close() {
        writer?.stop(safetyRecords(), close = true)
        writer = null
        port = null
    }

    private fun safetyRecords() = listOf(RobotProtocol.command("stop"),
        RobotProtocol.command("policy_disarm"), RobotProtocol.torqueAll(false))

    companion object {
        const val ROBOT_VENDOR_ID = 0x10C4
        const val ROBOT_PRODUCT_ID = 0xEA60
        const val BAUD = 2_000_000
    }
}
