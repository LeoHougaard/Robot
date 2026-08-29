package com.leo.pixelrobot.robot

import java.util.Locale

enum class ServoBatteryLevel {
    UNKNOWN,
    NORMAL,
    WARNING,
    CRITICAL,
}

data class ServoBatterySafetyStatus(
    val level: ServoBatteryLevel,
    val voltage: Float?,
    val live: Boolean,
) {
    val isLow: Boolean
        get() = level == ServoBatteryLevel.WARNING || level == ServoBatteryLevel.CRITICAL

    fun message(): String = when (level) {
        ServoBatteryLevel.UNKNOWN -> "Servo battery not detected"
        ServoBatteryLevel.NORMAL -> voltageMessage("Servo battery")
        ServoBatteryLevel.WARNING -> voltageMessage("LOW SERVO BATTERY - recharge soon")
        ServoBatteryLevel.CRITICAL -> voltageMessage("CRITICAL SERVO BATTERY - stop and recharge")
    }

    private fun voltageMessage(prefix: String): String {
        val reading = String.format(Locale.US, "%.1f V", requireNotNull(voltage))
        return "$prefix: $reading${if (live) "" else " (last idle reading)"}"
    }
}

object ServoBatterySafety {
    // Conservative loaded-voltage warnings for the robot's two-cell LiPo.
    // These are warnings only and are deliberately not motion interlocks.
    const val WARNING_VOLTAGE = 7.0f
    const val CRITICAL_VOLTAGE = 6.6f

    fun evaluate(voltage: Float?, live: Boolean): ServoBatterySafetyStatus {
        val level = when {
            voltage == null || !voltage.isFinite() -> ServoBatteryLevel.UNKNOWN
            voltage <= CRITICAL_VOLTAGE -> ServoBatteryLevel.CRITICAL
            voltage <= WARNING_VOLTAGE -> ServoBatteryLevel.WARNING
            else -> ServoBatteryLevel.NORMAL
        }
        return ServoBatterySafetyStatus(level, voltage?.takeIf(Float::isFinite), live)
    }
}
