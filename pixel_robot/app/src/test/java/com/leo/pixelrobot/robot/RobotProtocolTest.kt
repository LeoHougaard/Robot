package com.leo.pixelrobot.robot

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class RobotProtocolTest {
    @Test
    fun policyPreflightCommandsUseExplicitImuAndFullTorqueRequests() {
        val arm = JSONObject(RobotProtocol.arm().toString(Charsets.UTF_8))
        assertTrue(arm.getBoolean("compact_feedback"))
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
    fun servoTelemetryReadsOnlyTheChosenId() {
        val command = JSONObject(RobotProtocol.servoTelemetry(7).toString(Charsets.UTF_8))
        assertEquals("servo_telemetry", command.getString("cmd"))
        assertEquals(7, command.getInt("id"))
        assertTrue(runCatching { RobotProtocol.servoTelemetry(0) }.isFailure)
        assertTrue(runCatching { RobotProtocol.servoTelemetry(13) }.isFailure)
    }

    @Test
    fun maximumStandSequenceFitsInsideTheFirmwareJsonBuffer() {
        val poses = List(24) { step ->
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
        assertEquals(24, command.getJSONArray("steps").length())
        assertEquals(167.75, command.getJSONArray("steps").getJSONObject(23)
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
    fun streamedStandStepStaysFarBelowTheSerialReceiveBuffer() {
        val pose = (1..12).associateWith { servoId -> 150f + servoId * 0.25f }
        val bytes = RobotProtocol.programStep(pose, 80, 1_200, 30)
        val command = JSONObject(bytes.toString(Charsets.UTF_8))

        assertEquals("program_step", command.getString("cmd"))
        assertEquals(153.0, command.getJSONObject("poses").getDouble("12"), 1.0e-6)
        assertTrue(bytes.size < 512)
        assertEquals(
            "program_clear",
            JSONObject(RobotProtocol.programClear().toString(Charsets.UTF_8)).getString("cmd"),
        )
        assertEquals(
            "program_start",
            JSONObject(RobotProtocol.programStart().toString(Charsets.UTF_8)).getString("cmd"),
        )
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
