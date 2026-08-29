package com.leo.pixelrobot.robot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ServoBatterySafetyTest {
    @Test
    fun classifiesTwoCellLipoVoltageAtConservativeBoundaries() {
        assertEquals(ServoBatteryLevel.NORMAL, ServoBatterySafety.evaluate(7.1f, true).level)
        assertEquals(ServoBatteryLevel.WARNING, ServoBatterySafety.evaluate(7.0f, true).level)
        assertEquals(ServoBatteryLevel.WARNING, ServoBatterySafety.evaluate(6.7f, true).level)
        assertEquals(ServoBatteryLevel.CRITICAL, ServoBatterySafety.evaluate(6.6f, true).level)
        assertEquals(ServoBatteryLevel.UNKNOWN, ServoBatterySafety.evaluate(null, false).level)
    }

    @Test
    fun warningIncludesWhetherTheReadingIsStale() {
        val live = ServoBatterySafety.evaluate(6.9f, true)
        val idle = ServoBatterySafety.evaluate(6.9f, false)

        assertTrue(live.isLow)
        assertTrue(live.message().contains("LOW SERVO BATTERY"))
        assertFalse(live.message().contains("last idle reading"))
        assertTrue(idle.message().contains("last idle reading"))
    }
}
