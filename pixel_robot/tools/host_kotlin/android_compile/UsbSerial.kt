@file:Suppress("UNUSED_PARAMETER")
package com.hoho.android.usbserial.driver
import android.hardware.usb.*
import android.content.unavailable

/** Signatures reviewed against upstream tag 3.11.0, not an actual driver. */
interface UsbSerialPort {
    var dtr: Boolean
    var rts: Boolean
    fun open(connection: UsbDeviceConnection)
    fun close()
    fun setParameters(baud: Int, bits: Int, stop: Int, parity: Int)
    val readEndpoint: UsbEndpoint
    fun setReadQueue(count: Int, packetSize: Int)
    fun read(bytes: ByteArray, timeout: Int): Int
    fun write(bytes: ByteArray, timeout: Int)
    companion object { const val STOPBITS_1 = 1; const val PARITY_NONE = 0 }
}
interface UsbSerialDriver { val ports: List<UsbSerialPort> }
class UsbSerialProber {
    fun probeDevice(device: UsbDevice): UsbSerialDriver? = unavailable()
    companion object { fun getDefaultProber(): UsbSerialProber = unavailable() }
}
