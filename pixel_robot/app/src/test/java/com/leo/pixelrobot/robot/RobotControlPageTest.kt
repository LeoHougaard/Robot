package com.leo.pixelrobot.robot

import java.io.File
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class RobotControlPageTest {
    private val page = File("src/main/assets/robot_control.html").readText()

    @Test
    fun standAndPolicyDoNotRequirePoseConfirmation() {
        assertFalse(page.contains("id=\"secured\" type=\"checkbox\""))
        assertFalse(page.contains("id=\"exact-pose\" type=\"checkbox\""))
        assertFalse(page.contains("id=\"capture\""))
        assertFalse(page.contains("ROBOT_SECURELY_SUSPENDED"))
        assertFalse(page.contains("ROBOT_FIXED_IN_EXACT_START_POSE"))
        assertFalse(page.contains("/api/capture"))
        assertTrue(page.contains("post(\"/api/stand\")"))
        assertTrue(page.contains("post(\"/api/start\", values())"))
    }

    @Test
    fun allPhysicalActionsHaveAStopPath() {
        assertTrue(page.contains("id=\"stop\""))
        assertTrue(page.contains("navigator.sendBeacon(\"/api/stop\""))
        assertTrue(page.contains("if (powered)"))
    }

    @Test
    fun completedRunCanBeDownloadedWithoutExposingTheServerToTheLan() {
        assertTrue(page.contains("id=\"download\""))
        assertTrue(page.contains("/api/session/latest"))
        assertTrue(page.contains("latest_session_available"))
    }

    @Test
    fun servoBatteryWarningIsVisible() {
        assertTrue(page.contains("id=\"battery\""))
        assertTrue(page.contains("servo_battery_message"))
        assertTrue(page.contains("battery-critical"))
    }

    @Test
    fun oneServoIdCanBeSelectedForStandLoadTelemetry() {
        assertTrue(page.contains("id=\"servo-id\""))
        assertTrue(page.contains("/api/servo-telemetry"))
        assertTrue(page.contains("servo_telemetry_selected_id"))
        assertTrue(page.contains("not calibrated contact force"))
    }
}
