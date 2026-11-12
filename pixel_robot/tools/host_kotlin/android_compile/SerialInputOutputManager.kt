@file:Suppress("UNUSED_PARAMETER")
package com.hoho.android.usbserial.util
import com.hoho.android.usbserial.driver.UsbSerialPort
import android.content.unavailable

/** Compile-only signatures of upstream tag 3.11.0. No I/O or thread exists. */
class SerialInputOutputManager(port: UsbSerialPort, listener: Listener) {
    init { unavailable() }
    interface Listener {
        fun onNewData(data: ByteArray)
        fun onRunError(error: Exception)
    }
    fun setReadQueue(count: Int): Unit = unavailable()
    fun start(): Unit = unavailable()
    fun stop(): Unit = unavailable()
}
