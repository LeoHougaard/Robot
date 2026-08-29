package com.leo.pixelrobot.robot

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ServoTelemetryTest {
    @Test
    fun parsesEverySt3215RuntimeFeedbackField() {
        val telemetry = ServoTelemetry.fromJson(
            JSONObject()
                .put("type", "servo_telemetry")
                .put("id", 3)
                .put("name", "Left front knee")
                .put("sample_ms", 12_345)
                .put("position_raw", 2_048)
                .put("position_deg", 180.04)
                .put("joint_angle_deg", 179.96)
                .put("speed_raw", -12)
                .put("speed_rpm", -0.1758)
                .put("load_raw", -234)
                .put("load_percent", -23.4)
                .put("voltage_v", 7.4)
                .put("temperature_c", 31)
                .put("async_write_flag", 0)
                .put("servo_status", 0)
                .put("packet_status", 0)
                .put("moving", false)
                .put("current_raw", -70)
                .put("current_ma", -455.0)
                .put("estimated_torque_kg_cm", -2.379)
                .put("estimated_torque_nm", -0.2333),
            receivedAtMs = 55_000,
        )

        assertEquals(3, telemetry.id)
        assertEquals(-23.4f, telemetry.loadPercent, 1.0e-4f)
        assertEquals(-455.0f, telemetry.currentMilliamps, 1.0e-4f)
        assertEquals(-0.2333f, telemetry.estimatedTorqueNm, 1.0e-4f)
        assertEquals(55_000, telemetry.receivedAtMs)
        assertFalse(telemetry.toJson().getBoolean("force_calibrated"))
    }

    @Test
    fun parsesAllTwelveRuntimeRecordsFromOnePolicyFrame() {
        fun numbers(value: Number) = JSONArray().also { array -> repeat(12) { array.put(value) } }
        val ids = JSONArray().also { array -> (1..12).forEach(array::put) }
        val telemetry = ServoTelemetry.fromPolicyState(
            JSONObject()
                .put("type", "policy_state")
                .put("sample_ms", 12_345)
                .put("ids", ids)
                .put("angles_deg", numbers(179.96))
                .put("current_raw", numbers(-70))
                .put("status_errors", numbers(0)),
            receivedAtMs = 55_000,
        )

        assertEquals((1..12).toSet(), telemetry.keys)
        val servo3 = requireNotNull(telemetry[3])
        assertEquals(-455.0f, servo3.currentMilliamps, 1.0e-4f)
        assertEquals(-0.2333f, servo3.estimatedTorqueNm, 1.0e-4f)
        assertFalse(servo3.fullRuntimeFields)
        assertFalse(servo3.toJson().has("load_raw"))
    }

    @Test
    fun requiresFirmwareWithSynchronizedPolicyTelemetry() {
        assertFalse(FirmwareCapabilities.supportsPolicyServoTelemetry(null))
        assertFalse(FirmwareCapabilities.supportsPolicyServoTelemetry("0.1.9"))
        assertFalse(FirmwareCapabilities.supportsPolicyServoTelemetry("0.1.10"))
        assertFalse(FirmwareCapabilities.supportsPolicyServoTelemetry("0.1.11"))
        assertTrue(FirmwareCapabilities.supportsPolicyServoTelemetry("0.1.12"))
        assertTrue(FirmwareCapabilities.supportsPolicyServoTelemetry("0.2.0"))
        assertTrue(FirmwareCapabilities.supportsPolicyServoTelemetry("dev"))
        assertFalse(FirmwareCapabilities.supportsClockedPolicyFeedback("0.1.12"))
        assertTrue(FirmwareCapabilities.supportsClockedPolicyFeedback("0.1.13"))
        assertTrue(FirmwareCapabilities.supportsClockedPolicyFeedback("0.2.0"))
    }
}
