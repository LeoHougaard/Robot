@file:Suppress("UNUSED_PARAMETER")
package android.hardware.usb
import android.app.PendingIntent
import android.content.unavailable
class UsbDevice {
    val vendorId: Int get() = unavailable()
    val productId: Int get() = unavailable()
    val deviceId: Int get() = unavailable()
    val deviceName: String get() = unavailable()
    val productName: String? get() = unavailable()
}
class UsbDeviceConnection { fun close(): Unit = unavailable() }
class UsbManager {
    val deviceList: HashMap<String, UsbDevice> get() = unavailable()
    fun openDevice(device: UsbDevice): UsbDeviceConnection? = unavailable()
    fun hasPermission(device: UsbDevice): Boolean = unavailable()
    fun requestPermission(device: UsbDevice, intent: PendingIntent): Unit = unavailable()
    companion object {
        const val EXTRA_PERMISSION_GRANTED = "permission"
        const val EXTRA_DEVICE = "device"
        const val ACTION_USB_DEVICE_ATTACHED = "attach"
        const val ACTION_USB_DEVICE_DETACHED = "detach"
    }
}

class UsbEndpoint { val maxPacketSize: Int get() = unavailable() }
