package com.leo.pixelrobot.policy

import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test
import kotlin.random.Random

class StandProgramUploaderTest {
    @Test
    fun fullTwentyFourStepTrajectoryReachesAtomicStart() = runBlocking {
        val poses = trajectory(24)
        val firmware = FakeEsp32StandProtocol()

        StandProgramUploader.uploadAndStart(poses, 80, 1_200, 30, firmware::exchange)

        assertTrue(firmware.started)
        assertEquals(24, firmware.steps.size)
        assertEquals(poses.last()[12]!!.toDouble(), firmware.steps.last().getDouble("12"), 1.0e-4)
        assertTrue(firmware.maximumCommandBytes < 512)
        assertEquals(
            listOf("program_clear") + List(24) { "program_step" } + "program_start",
            firmware.commands,
        )
    }

    @Test
    fun rejectedMiddleStepNeverStartsAPartialTrajectory() = runBlocking {
        val firmware = FakeEsp32StandProtocol(rejectStepNumber = 7)

        val failure = runCatching {
            StandProgramUploader.uploadAndStart(trajectory(24), 80, 1_200, 30, firmware::exchange)
        }.exceptionOrNull()

        assertNotNull(failure)
        assertFalse(firmware.started)
        assertEquals(6, firmware.steps.size)
        assertFalse(firmware.commands.contains("program_start"))
    }

    @Test
    fun missingAcknowledgementNeverStartsMotion() = runBlocking {
        val firmware = FakeEsp32StandProtocol(dropAcknowledgementAtStep = 4)

        val failure = runCatching {
            StandProgramUploader.uploadAndStart(trajectory(12), 80, 1_200, 30, firmware::exchange)
        }.exceptionOrNull()

        assertNotNull(failure)
        assertFalse(firmware.started)
        assertFalse(firmware.commands.contains("program_start"))

        StandProgramUploader.uploadAndStart(trajectory(12), 80, 1_200, 30, firmware::exchange)
        assertTrue(firmware.started)
        assertEquals(12, firmware.steps.size)
    }

    @Test
    fun truncatedUsbJsonNeverStartsMotion() = runBlocking {
        val firmware = FakeEsp32StandProtocol(corruptStepNumber = 5)

        val failure = runCatching {
            StandProgramUploader.uploadAndStart(trajectory(24), 80, 1_200, 30, firmware::exchange)
        }.exceptionOrNull()

        assertNotNull(failure)
        assertFalse(firmware.started)
        assertEquals(4, firmware.steps.size)
        assertFalse(firmware.commands.contains("program_start"))
    }

    @Test
    fun emptyAndOversizedProgramsAreRejectedBeforeUsbTraffic() = runBlocking {
        var exchanges = 0
        val exchange: suspend (ByteArray, String) -> Unit = { _, _ -> exchanges++ }

        assertNotNull(
            runCatching { StandProgramUploader.uploadAndStart(emptyList(), 80, 1_200, 30, exchange) }
                .exceptionOrNull(),
        )
        assertNotNull(
            runCatching { StandProgramUploader.uploadAndStart(trajectory(25), 80, 1_200, 30, exchange) }
                .exceptionOrNull(),
        )
        assertEquals(0, exchanges)
    }

    @Test
    fun oneHundredDeterministicTrajectoriesCompleteWithoutOversizedMessages() = runBlocking {
        val random = Random(91_209)
        repeat(100) {
            val stepCount = random.nextInt(1, 25)
            val poses = List(stepCount) {
                (1..12).associateWith { random.nextDouble(90.0, 270.0).toFloat() }
            }
            val firmware = FakeEsp32StandProtocol()

            StandProgramUploader.uploadAndStart(poses, 80, 1_200, 30, firmware::exchange)

            assertTrue(firmware.started)
            assertEquals(stepCount, firmware.steps.size)
            assertTrue(firmware.maximumCommandBytes < 512)
        }
    }

    private fun trajectory(stepCount: Int): List<Map<Int, Float>> = List(stepCount) { step ->
        (1..12).associateWith { servoId -> 120f + servoId * 2f + step * 0.125f }
    }

    private class FakeEsp32StandProtocol(
        private val rejectStepNumber: Int? = null,
        private val dropAcknowledgementAtStep: Int? = null,
        private val corruptStepNumber: Int? = null,
    ) {
        val commands = mutableListOf<String>()
        val steps = mutableListOf<JSONObject>()
        var started = false
        var maximumCommandBytes = 0
        private var acknowledgementDropped = false

        suspend fun exchange(bytes: ByteArray, expectedAcknowledgement: String) {
            maximumCommandBytes = maxOf(maximumCommandBytes, bytes.size)
            val raw = bytes.toString(Charsets.UTF_8)
            val isStep = raw.contains("\"cmd\":\"program_step\"")
            val stepNumber = steps.size + 1
            val received = if (isStep && stepNumber == corruptStepNumber) raw.dropLast(5) else raw
            val message = JSONObject(received)
            val command = message.getString("cmd")
            commands += command
            when (command) {
                "program_clear" -> {
                    steps.clear()
                    started = false
                }
                "program_step" -> {
                    if (stepNumber == rejectStepNumber) error("ESP32 rejected program_step $stepNumber")
                    val poses = message.getJSONObject("poses")
                    require(poses.length() == 12)
                    require((1..12).all { poses.has(it.toString()) })
                    require(message.getInt("ms") == 80)
                    require(message.getInt("speed") == 1_200)
                    require(message.getInt("accel") == 30)
                    steps += poses
                    if (stepNumber == dropAcknowledgementAtStep && !acknowledgementDropped) {
                        acknowledgementDropped = true
                        error("ESP32 acknowledgement timed out")
                    }
                }
                "program_start" -> {
                    require(steps.isNotEmpty())
                    started = true
                }
                else -> error("unexpected command $command")
            }
            assertEquals(command, expectedAcknowledgement)
        }
    }
}
