package com.leo.pixelrobot.host

import android.content.Context
import android.hardware.usb.UsbManager
import android.os.SystemClock
import androidx.core.app.NotificationCompat
import com.hoho.android.usbserial.driver.UsbSerialProber
import org.junit.Assert.assertThrows
import org.junit.Test

/** The signature-only compile path must never impersonate working device APIs. */
class AndroidCompileBoundaryTest {
    @Test fun monotonicClocksCannotSupplyFakeDeviceTiming() {
        assertThrows(UnsupportedOperationException::class.java) { SystemClock.elapsedRealtime() }
        assertThrows(UnsupportedOperationException::class.java) { SystemClock.elapsedRealtimeNanos() }
    }

    @Test fun serviceContextCannotOpenAndroidAssets() {
        assertThrows(UnsupportedOperationException::class.java) { Context().assets }
    }

    @Test fun serviceContextCannotObtainAUsbDeviceManager() {
        assertThrows(UnsupportedOperationException::class.java) { Context().getSystemService(UsbManager::class.java) }
    }

    @Test fun serialDriverProbingCannotBecomeAHostHardwarePath() {
        assertThrows(UnsupportedOperationException::class.java) { UsbSerialProber.getDefaultProber() }
    }

    @Test fun notificationShimDoesNotImpersonateAServiceLifecycle() {
        assertThrows(UnsupportedOperationException::class.java) { NotificationCompat.Builder(Context(), "compile-only") }
    }
}
