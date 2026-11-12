package com.leo.pixelrobot

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import android.os.SystemClock
import androidx.test.platform.app.InstrumentationRegistry
import com.leo.pixelrobot.policy.ShadowServiceState
import com.leo.pixelrobot.robot.RobotService
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import org.junit.Assert.*
import org.junit.Test

/** Installed service/clock/lifecycle checks. No provider or physical-control entrypoint. */
class ShadowPreviewDeviceTest {
    private fun withService(test: (RobotService) -> Unit) {
        val context = InstrumentationRegistry.getInstrumentation().targetContext
        val ready = CountDownLatch(1)
        var service: RobotService? = null
        val connection = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
                service = (binder as RobotService.LocalBinder).service; ready.countDown()
            }
            override fun onServiceDisconnected(name: ComponentName?) { service = null }
        }
        val intent = Intent(context, RobotService::class.java)
        assertTrue(context.bindService(intent, connection, Context.BIND_AUTO_CREATE))
        try { assertTrue(ready.await(5, TimeUnit.SECONDS)); test(requireNotNull(service)) }
        finally { context.unbindService(connection); context.stopService(intent) }
    }
    @Test fun serviceBindsLoadedAssetsAndBackgroundingRevokesPreview() = withService { service ->
        val handle = service.beginShadowRequest("Authored device preview", true)
        val status = service.submitShadowResponse(handle,
            """{"schema_version":1,"kind":"velocity_request","frame":"body","forward_m_s":0.02,"lateral_m_s":0,"yaw_rate_rad_s":0,"duration_ms":500}""") { true }
        assertEquals(ShadowServiceState.SHADOW_ACCEPTED, status.state)
        assertEquals(handle.capabilityDigest, status.capabilityDigest)
        assertEquals(5, status.assetSha256.size)
        assertFalse(status.physicalExecution); assertFalse(status.policyActorInference)
        assertFalse(service.policyStatus.value.active)
        service.onShadowOperatorBackgrounded()
        assertEquals(ShadowServiceState.CANCELLED, service.shadowStatus.value.state)
        assertEquals("operator_backgrounded", service.shadowStatus.value.reason)
    }
    @Test fun replacementAndStopRejectLateInstalledCallbacks() = withService { service ->
        val old = service.beginShadowRequest("Old authored preview", true)
        val next = service.beginShadowRequest("New authored preview", true)
        service.submitShadowResponse(old, """{"schema_version":1,"kind":"stop_request"}""") { true }
        assertEquals(next.requestId, service.shadowStatus.value.requestId)
        assertEquals(ShadowServiceState.PENDING, service.shadowStatus.value.state)
        service.stopShadowPreview()
        service.submitShadowResponse(next, """{"schema_version":1,"kind":"stop_request"}""") { true }
        assertEquals(ShadowServiceState.STOP_REQUESTED, service.shadowStatus.value.state)
        assertFalse(service.shadowStatus.value.physicalStopConfirmed)
        assertFalse(service.policyStatus.value.active)
    }
    @Test fun installedServiceExpiresAcceptedPreviewWithoutAnotherCallback() = withService { service ->
        val handle = service.beginShadowRequest("Short authored preview", true)
        service.submitShadowResponse(handle,
            """{"schema_version":1,"kind":"velocity_request","frame":"body","forward_m_s":0.02,"lateral_m_s":0,"yaw_rate_rad_s":0,"duration_ms":100}""") { true }
        val deadline = SystemClock.elapsedRealtime() + 2000
        while (service.shadowStatus.value.state == ShadowServiceState.SHADOW_ACCEPTED &&
            SystemClock.elapsedRealtime() < deadline) SystemClock.sleep(20)
        assertEquals(ShadowServiceState.EXPIRED, service.shadowStatus.value.state)
        assertNull(service.shadowStatus.value.preview)
        assertFalse(service.policyStatus.value.active)
    }
}
