package com.leo.pixelrobot.robot

import java.util.ArrayDeque
import java.util.concurrent.Executor
import org.junit.Assert.*
import org.junit.Test

class UsbStartupFenceTest {
    private val nonce = "0123456789abcdef0123456789abcdef"
    private val ack = "{\"type\":\"serial_abort\",\"version\":2,\"nonce\":\"$nonce\"}"
    private fun challenge() = byteArrayOf(0x18) + nonce.toByteArray(Charsets.US_ASCII)
    private fun acknowledge(fence: UsbStartupFence) = fence.acceptBeforeReady(
        (ack + "  \r\n").toByteArray())
    private fun started(): UsbStartupFence = UsbStartupFence(nonce).also {
        assertArrayEquals(challenge(), it.frameForNativeWrite(challenge()))
    }
    @Test fun challengeContainsNoJsonDelimiter() {
        assertArrayEquals(challenge(), UsbStartupFence(nonce).challenge())
        assertEquals(33, UsbStartupFence(nonce).challenge().size)
        assertFalse(challenge().contains('\n'.code.toByte()))
    }
    @Test fun ordinaryFirstWriteIsRejectedBeforeAnyDelimiter() {
        val fence = UsbStartupFence(nonce)
        assertThrows(IllegalStateException::class.java) { fence.frameForNativeWrite("{\"cmd\":\"hello\"}\n".toByteArray()) }
        assertFalse(fence.isReady())
    }
    @Test fun noSecondWriteWithoutCapabilityAcknowledgement() {
        val fence = started()
        assertThrows(IllegalStateException::class.java) { fence.frameForNativeWrite("stop\n".toByteArray()) }
        assertFalse(fence.acknowledged.isDone)
    }
    @Test fun exactAcknowledgementAllowsSingleResynchronizingDelimiter() {
        val fence = started(); acknowledge(fence)
        assertTrue(fence.acknowledged.isDone); assertTrue(fence.isReady())
        assertEquals("\nhello\n", fence.frameForNativeWrite("hello\n".toByteArray()).toString(Charsets.UTF_8))
        assertEquals("next\n", fence.frameForNativeWrite("next\n".toByteArray()).toString(Charsets.UTF_8))
    }
    @Test fun fragmentedPaddedAcknowledgementIsAccepted() {
        val fence = started(); val expected = ack
        expected.forEach { fence.acceptBeforeReady(it.toString().toByteArray()) }
        assertFalse(fence.isReady()); fence.acceptBeforeReady("       \r\n".toByteArray()); assertTrue(fence.isReady())
    }
    @Test fun helloAndOldPolicyTrafficCannotGrantNewFramingCapability() {
        val fence = started()
        fence.acceptBeforeReady("{\"type\":\"hello\",\"supportsSerialInputAbort\":true}\n{\"type\":\"policy_disarmed\"}\n".toByteArray())
        assertFalse(fence.isReady()); assertFalse(fence.acknowledged.isDone)
    }
    @Test fun wrongVersionMalformedAndPrefixOnlyAcknowledgementsAreRejected() {
        listOf(
            "{\"type\":\"serial_abort\",\"version\":1}",
            ack.replace(nonce, "f".repeat(32)),
            "{\"type\":\"serial_abort\",\"version\":\"1\"}",
            "{\"type\":\"serial_abort\",\"version\":1,\"extra\":true}",
            ack + "{}",
            ack + "garbage",
        ).forEach { line ->
            val fence = started(); fence.acceptBeforeReady((line + "\n").toByteArray()); assertFalse(line, fence.isReady())
        }
    }
    @Test fun acknowledgementsBeforeChallengeAreIgnored() {
        val fence = UsbStartupFence(nonce); acknowledge(fence)
        fence.frameForNativeWrite(challenge()); assertFalse(fence.isReady())
        acknowledge(fence); assertTrue(fence.isReady())
    }
    @Test fun oversizedTrafficFailsClosedAndCannotGrowUnbounded() {
        val fence = started(); fence.acceptBeforeReady(("x".repeat(1_025) + ack + "\n").toByteArray())
        assertFalse(fence.isReady()); acknowledge(fence); assertTrue(fence.isReady())
    }
    @Test fun cancellationBeforeAckForbidsSafetyAndLateAck() {
        val fence = started(); assertFalse(fence.permitSafetyOrAbort(IllegalStateException("timeout")))
        acknowledge(fence); assertFalse(fence.isReady()); assertTrue(fence.acknowledged.isCompletedExceptionally)
        assertThrows(IllegalStateException::class.java) { fence.frameForNativeWrite("stop\n".toByteArray()) }
    }
    @Test fun cancellationBeforeChallengeForbidsEveryWrite() {
        val fence = UsbStartupFence(nonce); assertFalse(fence.permitSafetyOrAbort(IllegalStateException("closed")))
        assertThrows(IllegalStateException::class.java) { fence.frameForNativeWrite(challenge()) }
    }
    @Test fun stopAfterVerifiedAckCanSendExistingSafetyBatch() {
        val fence = started(); acknowledge(fence)
        assertTrue(fence.permitSafetyOrAbort(IllegalStateException("stop")))
        assertEquals("\nstop\ndisarm\noff\n", fence.frameForNativeWrite("stop\ndisarm\noff\n".toByteArray()).toString(Charsets.UTF_8))
    }
    @Test fun unknownLegacyFirmwareCloseNeverCompletesBufferedDangerousJson() {
        val pending = StringBuilder("{\"cmd\":\"servo_torque\",\"all\":true,\"enabled\":true}")
        val nativeWrites = mutableListOf<ByteArray>(); val work = ArrayDeque<Runnable>(); val closes = ArrayDeque<Runnable>()
        val fence = UsbStartupFence(nonce)
        val writer = BoundedUsbWriter(object : UsbWriteSink {
            override fun write(bytes: ByteArray, timeoutMs: Int) {
                val framed = fence.frameForNativeWrite(bytes); nativeWrites += framed
                // Legacy firmware dispatches only on LF. No LF must reach this stale JSON.
                assertFalse(framed.contains('\n'.code.toByte())); pending.append(framed.toString(Charsets.UTF_8))
            }
            override fun close() = Unit
        }, Executor { work.add(it) }, Executor { closes.add(it) },
            UsbDeadlineScheduler { _, _ -> UsbDeadlineCancellation {} }, "stop\ndisarm\noff\n".toByteArray())
        writer.write(challenge()); while (work.isNotEmpty()) work.removeFirst().run()
        assertFalse(fence.permitSafetyOrAbort(IllegalStateException("legacy firmware no ACK")))
        writer.fail(IllegalStateException("abort capability absent"))
        val close = writer.closeAsync(); while (work.isNotEmpty()) work.removeFirst().run()
        while (closes.isNotEmpty()) closes.removeFirst().run()
        assertEquals(1, nativeWrites.size); assertArrayEquals(challenge(), nativeWrites.single())
        assertTrue(close.quiesced.isDone); assertFalse(close.outcome.join().safetyWriteCompleted)
        assertFalse(pending.contains('\n'))
    }
    @Test fun everyDefaultConnectionUsesFreshNonce() {
        val first = UsbStartupFence().challenge(); val second = UsbStartupFence().challenge()
        assertEquals(33, first.size); assertFalse(first.contentEquals(second))
        assertTrue(first.drop(1).all { it.toInt().toChar() in "0123456789abcdef" })
    }
    @Test fun malformedNonceCannotBecomeChallenge() {
        listOf("", "a".repeat(31), "a".repeat(33), "A".repeat(32), "\n".repeat(32)).forEach {
            assertThrows(IllegalArgumentException::class.java) { UsbStartupFence(it) }
        }
    }
    @Test fun bufferedPreviousConnectionNonceIsNeverAccepted() {
        val fence = started()
        fence.acceptBeforeReady((ack.replace(nonce, "f".repeat(32)) + "\n").toByteArray())
        assertFalse(fence.isReady()); acknowledge(fence); assertTrue(fence.isReady())
    }
    @Test fun lostAcknowledgementBytePreventsDelimiterPermission() {
        val fence = started(); fence.acceptBeforeReady((ack.dropLast(1) + "\n").toByteArray())
        assertFalse(fence.isReady()); assertFalse(fence.permitSafetyOrAbort(IllegalStateException("timeout")))
        assertThrows(IllegalStateException::class.java) { fence.frameForNativeWrite("hello\n".toByteArray()) }
    }

}
