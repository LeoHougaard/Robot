package com.leo.pixelrobot.robot

import java.util.concurrent.CompletableFuture
import java.security.SecureRandom

/**
 * A delimiter-free capability challenge protects reconnect to unknown/older firmware.
 * CAN plus a fresh fixed-length hex nonce cannot terminate a complete old JSON record.
 * Only firmware that immediately discards its pending input and echoes that nonce is allowed to receive the first LF.
 * A missing capability closes the link without a safety batch (which contains LF).
 */
class UsbStartupFence(private val nonce: String = randomNonce()) {
    init { require(nonce.matches(Regex("[0-9a-f]{32}"))) { "USB startup nonce must be 32 lowercase hex bytes" } }
    private val expectedAcknowledgement = "{\"type\":\"serial_abort\",\"version\":2,\"nonce\":\"$nonce\"}"
    private enum class State { NEW, AWAITING_ABORT_ACK, READY, FAILED }
    private var state = State.NEW
    private val decoder = JsonLineDecoder(1_024)
    private var needsDelimiter = true
    val acknowledged = CompletableFuture<Unit>()

    @Synchronized
    fun frameForNativeWrite(bytes: ByteArray): ByteArray {
        if (state == State.NEW) {
            check(bytes.contentEquals(challenge())) { "USB must begin with delimiter-free CAN" }
            state = State.AWAITING_ABORT_ACK
            return bytes.copyOf()
        }
        check(state == State.READY) { "USB firmware abort capability is unverified" }
        return if (needsDelimiter) {
            needsDelimiter = false
            byteArrayOf(0x0a) + bytes
        } else bytes
    }

    /** Before readiness, all other firmware traffic (including hello) is ignored. */
    fun acceptBeforeReady(bytes: ByteArray) {
        val matched = synchronized(this) {
            if (state != State.AWAITING_ABORT_ACK) false else {
                val accepted = decoder.accept(bytes).any { it == expectedAcknowledgement }
                if (accepted) state = State.READY
                accepted
            }
        }
        if (matched) acknowledged.complete(Unit)
    }

    @Synchronized
    fun isReady(): Boolean = state == State.READY

    /** Atomically decide whether newline-bearing safety writes are permissible. */
    fun permitSafetyOrAbort(error: Throwable): Boolean {
        val ready = synchronized(this) {
            if (state == State.READY) true else { state = State.FAILED; decoder.reset(); false }
        }
        if (!ready) acknowledged.completeExceptionally(error)
        return ready
    }

    fun challenge(): ByteArray = byteArrayOf(0x18) + nonce.toByteArray(Charsets.US_ASCII)

    companion object {
        private fun randomNonce(): String = ByteArray(16).also { SecureRandom().nextBytes(it) }
            .joinToString("") { (it.toInt() and 0xff).toString(16).padStart(2, '0') }
    }
}
