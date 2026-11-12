@file:Suppress("UNUSED_PARAMETER")
package android.content.pm
import android.content.unavailable
class PackageManager { fun getPackageInfo(name: String, flags: Int): PackageInfo = unavailable() }
class PackageInfo {
    val versionName: String? get() = unavailable()
    val longVersionCode: Long get() = unavailable()
}
object ServiceInfo { const val FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE = 16 }
