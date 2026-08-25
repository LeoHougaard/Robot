package com.leo.pixelrobot.robot

import android.hardware.usb.UsbDevice
import android.hardware.usb.UsbManager
import com.hoho.android.usbserial.driver.UsbSerialPort
import com.hoho.android.usbserial.driver.UsbSerialProber
import com.hoho.android.usbserial.util.SerialInputOutputManager
import java.io.Closeable

class UsbRobotTransport(
    private val manager: UsbManager,
    private val device: UsbDevice,
    private val onBytes: (ByteArray) -> Unit,
    private val onFailure: (Throwable) -> Unit,
) : Closeable, SerialInputOutputManager.Listener {
    private var port: UsbSerialPort? = null
    private var ioManager: SerialInputOutputManager? = null

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
            ioManager = SerialInputOutputManager(openedPort, this).also { it.start() }
        } catch (error: Throwable) {
            connection.close()
            throw error
        }
    }

    @Synchronized
    fun write(bytes: ByteArray) {
        ioManager?.writeAsync(bytes) ?: error("USB serial is not open")
    }

    override fun onNewData(data: ByteArray) = onBytes(data)

    override fun onRunError(error: Exception) = onFailure(error)

    @Synchronized
    override fun close() {
        ioManager?.stop()
        ioManager = null
        runCatching { port?.close() }
        port = null
    }

    companion object {
        const val ROBOT_VENDOR_ID = 0x10C4
        const val ROBOT_PRODUCT_ID = 0xEA60
        const val BAUD = 921_600
    }
}
