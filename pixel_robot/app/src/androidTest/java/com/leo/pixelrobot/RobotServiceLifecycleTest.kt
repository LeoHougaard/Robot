package com.leo.pixelrobot

import android.content.Intent
import android.os.SystemClock
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.leo.pixelrobot.robot.RobotService
import java.net.HttpURLConnection
import java.net.URL
import org.json.JSONObject
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class RobotServiceLifecycleTest {
    @Test
    fun startsSafelyWithoutUsbPermission() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val intent = Intent(context, RobotService::class.java)
        check(context.startService(intent) != null)
        try {
            val deadline = SystemClock.elapsedRealtime() + 5_000
            var status: JSONObject? = null
            while (status == null && SystemClock.elapsedRealtime() < deadline) {
                status = runCatching {
                    val connection = URL("http://127.0.0.1:8767/api/status").openConnection() as HttpURLConnection
                    connection.connectTimeout = 500
                    connection.readTimeout = 500
                    try {
                        check(connection.responseCode == 200)
                        JSONObject(connection.inputStream.bufferedReader().use { it.readText() })
                    } finally {
                        connection.disconnect()
                    }
                }.getOrNull()
                if (status == null) SystemClock.sleep(100)
            }
            checkNotNull(status)
            check(status.getBoolean("ok"))
            check(!status.getBoolean("policy_armed"))

            // Setting all six commands must work without starting motion.
            // An invalid posture must be rejected by the installed contract.
            for ((roll, expectedCode) in listOf(0.0 to 200, 1e10 to 400)) {
                val command = URL("http://127.0.0.1:8767/api/command").openConnection() as HttpURLConnection
                command.connectTimeout = 500
                command.readTimeout = 500
                command.requestMethod = "POST"
                command.doOutput = true
                try {
                    val body = JSONObject().put("forward", 0).put("lateral", 0).put("yaw_rate", 0)
                        .put("height_offset", 0).put("roll", roll).put("pitch", 0).toString()
                    command.outputStream.use { it.write(body.toByteArray()) }
                    check(command.responseCode == expectedCode)
                } finally { command.disconnect() }
            }

            val stopConnection = URL("http://127.0.0.1:8767/api/stop").openConnection() as HttpURLConnection
            stopConnection.connectTimeout = 500
            stopConnection.readTimeout = 500
            stopConnection.requestMethod = "POST"
            stopConnection.doOutput = true
            stopConnection.setRequestProperty("Content-Type", "application/json")
            val stopped = try {
                stopConnection.outputStream.use { it.write("{}".toByteArray()) }
                check(stopConnection.responseCode == 200)
                JSONObject(stopConnection.inputStream.bufferedReader().use { it.readText() })
            } finally {
                stopConnection.disconnect()
            }
            check(!stopped.getBoolean("policy_armed"))
            check(!stopped.getBoolean("policy_active"))
            check(!stopped.getBoolean("policy_commanding"))
            check(stopped.getString("policy_detail").contains("Stop requested"))
        } finally {
            check(context.stopService(intent))
        }
    }
}
