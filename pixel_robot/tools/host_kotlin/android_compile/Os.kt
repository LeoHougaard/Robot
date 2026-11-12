package android.os
import android.content.unavailable
interface IBinder
open class Binder : IBinder
class PowerManager { val currentThermalStatus: Int get() = unavailable() }
object BatteryManager {
    const val EXTRA_LEVEL = "level"
    const val EXTRA_SCALE = "scale"
    const val EXTRA_VOLTAGE = "voltage"
    const val EXTRA_TEMPERATURE = "temperature"
    const val EXTRA_PLUGGED = "plugged"
}
object Build {
    val MANUFACTURER: String get() = unavailable()
    val MODEL: String get() = unavailable()
    val DEVICE: String get() = unavailable()
    object VERSION {
        val SDK_INT: Int get() = unavailable()
        val RELEASE: String get() = unavailable()
    }
}
