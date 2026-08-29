package com.leo.pixelrobot.policy

import com.leo.pixelrobot.robot.RobotProtocol

internal object StandProgramUploader {
    suspend fun uploadAndStart(
        poses: List<Map<Int, Float>>,
        stepMilliseconds: Int,
        speed: Int,
        acceleration: Int,
        exchange: suspend (command: ByteArray, expectedAcknowledgement: String) -> Unit,
    ) {
        require(poses.isNotEmpty() && poses.size <= MAX_STEPS)
        exchange(RobotProtocol.programClear(), "program_clear")
        poses.forEach { pose ->
            exchange(
                RobotProtocol.programStep(
                    pose,
                    stepMilliseconds = stepMilliseconds,
                    speed = speed,
                    acceleration = acceleration,
                ),
                "program_step",
            )
        }
        exchange(RobotProtocol.programStart(), "program_start")
    }

    private const val MAX_STEPS = 24
}
