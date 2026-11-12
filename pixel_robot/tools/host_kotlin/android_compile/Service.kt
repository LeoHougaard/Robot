@file:Suppress("UNUSED_PARAMETER")
package android.app
import android.content.*
import android.os.IBinder
open class Service : Context() {
    open fun onCreate(): Unit = unavailable()
    open fun onDestroy(): Unit = unavailable()
    open fun onTaskRemoved(rootIntent: Intent?): Unit = unavailable()
    open fun onBind(intent: Intent?): IBinder? = unavailable()
    open fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = unavailable()
    fun stopForeground(flags: Int): Unit = unavailable()
    companion object { const val START_STICKY = 1; const val STOP_FOREGROUND_REMOVE = 1 }
}
class Notification
class NotificationChannel(id: String, name: String, importance: Int) {
    init { unavailable() }
}
class NotificationManager {
    fun createNotificationChannel(channel: NotificationChannel): Unit = unavailable()
    fun notify(id: Int, notification: Notification): Unit = unavailable()
    companion object { const val IMPORTANCE_LOW = 2 }
}
class PendingIntent {
    companion object {
        const val FLAG_UPDATE_CURRENT = 1 shl 27
        const val FLAG_IMMUTABLE = 1 shl 26
        const val FLAG_MUTABLE = 1 shl 25
        fun getActivity(context: Context, code: Int, intent: Intent, flags: Int): PendingIntent = unavailable()
        fun getService(context: Context, code: Int, intent: Intent, flags: Int): PendingIntent = unavailable()
        fun getBroadcast(context: Context, code: Int, intent: Intent, flags: Int): PendingIntent = unavailable()
    }
}
