package com.leo.pixelrobot.robot

enum class LinkState {
    SEARCHING,
    PERMISSION_REQUIRED,
    OPENING,
    HANDSHAKING,
    READY,
    ERROR,
}

data class RobotStatus(
    val linkState: LinkState = LinkState.SEARCHING,
    val detail: String = "Looking for the ESP32",
    val deviceName: String? = null,
    val firmwareVersion: String? = null,
    val policyArmed: Boolean = false,
    val lastSequence: Long? = null,
    val feedbackComplete: Boolean? = null,
    val lastMessageAtMs: Long? = null,
)

