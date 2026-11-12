@file:Suppress("UNUSED_PARAMETER")
package android.util
import android.content.unavailable
object Log { fun e(tag: String, message: String, error: Throwable): Int = unavailable() }
