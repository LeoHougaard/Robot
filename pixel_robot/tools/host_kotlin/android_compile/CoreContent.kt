@file:Suppress("UNUSED_PARAMETER")
package androidx.core.content
import android.content.*
object ContextCompat {
    const val RECEIVER_NOT_EXPORTED = 4
    fun registerReceiver(context: Context, receiver: BroadcastReceiver, filter: IntentFilter, flags: Int): Intent? = unavailable()
}
