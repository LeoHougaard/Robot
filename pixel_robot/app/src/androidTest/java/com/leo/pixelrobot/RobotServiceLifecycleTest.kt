package com.leo.pixelrobot

import android.content.Intent
import android.os.SystemClock
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.leo.pixelrobot.robot.RobotService
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class RobotServiceLifecycleTest {
    @Test
    fun startsSafelyWithoutUsbPermission() {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val intent = Intent(context, RobotService::class.java)
        check(context.startService(intent) != null)
        SystemClock.sleep(500)
        check(context.stopService(intent))
    }
}
