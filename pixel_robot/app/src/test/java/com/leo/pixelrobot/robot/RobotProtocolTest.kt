package com.leo.pixelrobot.robot

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RobotProtocolTest {
    @Test
    fun policyPreflightCommandsUseExplicitImuAndFullTorqueRequests() {
        assertEquals(
            "imu_status",
            JSONObject(RobotProtocol.imuStatus().toString(Charsets.UTF_8)).getString("cmd"),
        )
        val torque = JSONObject(RobotProtocol.torqueLimitAll(1_000).toString(Charsets.UTF_8))
        assertEquals("servo_torque_limit", torque.getString("cmd"))
        assertEquals(1_000, torque.getInt("limit"))
        assertTrue(torque.getBoolean("all"))
    }

    @Test
    fun capturedZeroSequenceFitsInsideTheFirmwareJsonBuffer() {
        val poses = List(15) { step ->
            (1..12).associateWith { servoId -> 150f + servoId + step * 0.25f }
        }
        val bytes = RobotProtocol.playPoseSequence(
            poses,
            stepMilliseconds = 200,
            speed = 180,
            acceleration = 30,
        )
        val command = JSONObject(bytes.toString(Charsets.UTF_8))
        assertEquals("play", command.getString("cmd"))
        assertEquals(15, command.getJSONArray("steps").length())
        assertEquals(165.5, command.getJSONArray("steps").getJSONObject(14)
            .getJSONObject("poses").getDouble("12"), 1.0e-6)
        assertTrue(bytes.size < 6_144)
    }

    @Test
    fun capturedZeroSequenceRejectsMoreThanFirmwareCanStore() {
        val pose = (1..12).associateWith { 180f }
        val error = runCatching {
            RobotProtocol.playPoseSequence(List(25) { pose }, 200, 180, 30)
        }.exceptionOrNull()
        assertTrue(error is IllegalArgumentException)
    }

    @Test
    fun firmwareCalibrationFitsTheCommandBuffer() {
        val servos = JSONArray((1..12).map { servoId ->
            JSONObject()
                .put("id", servoId)
                .put("name", "servo $servoId")
                .put("min", 120.25)
                .put("home", 180.5)
                .put("max", 240.75)
                .put("invert", false)
                .put("enabled", true)
                .put("monitor", true)
                .put("monitorInterval", 250)
        })
        val bytes = RobotProtocol.configSet(servos)
        val command = JSONObject(bytes.toString(Charsets.UTF_8))
        assertEquals("config_set", command.getString("cmd"))
        assertEquals(12, command.getJSONArray("servos").length())
        assertTrue(bytes.size < 6_144)
    }
}
