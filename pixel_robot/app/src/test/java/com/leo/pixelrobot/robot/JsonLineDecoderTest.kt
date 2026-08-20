package com.leo.pixelrobot.robot

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class JsonLineDecoderTest {
    @Test
    fun preservesFragmentsAndStripsFirmwarePadding() {
        val decoder = JsonLineDecoder()
        assertTrue(decoder.accept("{\"type\":\"hel".toByteArray()).isEmpty())
        assertEquals(
            listOf("{\"type\":\"hello\"}"),
            decoder.accept("lo\"}        \r\n".toByteArray()),
        )
    }

    @Test
    fun returnsEveryCompleteLine() {
        val decoder = JsonLineDecoder()
        assertEquals(listOf("{}", "{\"seq\":1}"), decoder.accept("{}\n{\"seq\":1}\n".toByteArray()))
    }

    @Test
    fun rejectsAnOversizedUnterminatedFrame() {
        val decoder = JsonLineDecoder(maximumBytes = 8)
        assertTrue(decoder.accept(ByteArray(9) { 'x'.code.toByte() }).isEmpty())
        assertEquals(listOf("{}"), decoder.accept("{}\n".toByteArray()))
    }
}

