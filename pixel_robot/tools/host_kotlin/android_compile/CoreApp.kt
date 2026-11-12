@file:Suppress("UNUSED_PARAMETER")
package androidx.core.app
import android.app.*
import android.content.*
object ServiceCompat {
    fun startForeground(service: Service, id: Int, notification: Notification, types: Int): Unit = unavailable()
}
class NotificationCompat {
    class Builder(context: Context, channel: String) {
        init { unavailable() }
        fun setSmallIcon(id: Int): Builder = unavailable()
        fun setContentTitle(text: String): Builder = unavailable()
        fun setContentText(text: String): Builder = unavailable()
        fun setStyle(style: BigTextStyle): Builder = unavailable()
        fun setContentIntent(intent: PendingIntent): Builder = unavailable()
        fun addAction(icon: Int, title: String, intent: PendingIntent): Builder = unavailable()
        fun setOngoing(value: Boolean): Builder = unavailable()
        fun build(): Notification = unavailable()
    }
    class BigTextStyle { fun bigText(text: String): BigTextStyle = unavailable() }
}
