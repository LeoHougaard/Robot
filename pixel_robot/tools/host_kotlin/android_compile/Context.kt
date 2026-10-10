@file:Suppress("UNUSED_PARAMETER")
package android.content

import android.content.res.AssetManager
import android.content.pm.PackageManager
import java.io.File

/** Compile signatures only. These shims are not Android emulation. */
internal fun unavailable(): Nothing = throw UnsupportedOperationException("Compile-only Android boundary")
open class Context {
    val assets: AssetManager get() = unavailable()
    val filesDir: File get() = unavailable()
    val packageName: String get() = unavailable()
    val packageManager: PackageManager get() = unavailable()
    fun <T> getSystemService(type: Class<T>): T = unavailable()
    fun registerReceiver(receiver: BroadcastReceiver?, filter: IntentFilter): Intent? = unavailable()
    fun unregisterReceiver(receiver: BroadcastReceiver): Unit = unavailable()
}
abstract class BroadcastReceiver { abstract fun onReceive(context: Context, intent: Intent) }
class Intent {
    constructor()
    constructor(action: String)
    constructor(context: Context, type: Class<*>)
    val action: String? get() = unavailable()
    fun setAction(action: String): Intent = unavailable()
    fun setPackage(value: String): Intent = unavailable()
    fun getBooleanExtra(name: String, default: Boolean): Boolean = unavailable()
    fun getIntExtra(name: String, default: Int): Int = unavailable()
    fun <T> getParcelableExtra(name: String, type: Class<T>): T? = unavailable()
    fun <T> getParcelableExtra(name: String): T? = unavailable()
    companion object { const val ACTION_BATTERY_CHANGED = "compile-only" }
}
class IntentFilter {
    constructor()
    constructor(action: String)
    fun addAction(action: String): Unit = unavailable()
}
