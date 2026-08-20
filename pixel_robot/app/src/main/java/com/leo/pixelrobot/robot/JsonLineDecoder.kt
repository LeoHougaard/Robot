package com.leo.pixelrobot.robot

/** Preserves partial reads and strips the ESP32's 64-byte packet padding. */
class JsonLineDecoder(private val maximumBytes: Int = 16_384) {
    private val pending = ArrayList<Byte>()

    @Synchronized
    fun accept(bytes: ByteArray): List<String> {
        if (pending.size + bytes.size > maximumBytes) {
            pending.clear()
            return emptyList()
        }
        bytes.forEach(pending::add)
        val lines = mutableListOf<String>()
        while (true) {
            val newline = pending.indexOf('\n'.code.toByte())
            if (newline < 0) break
            val raw = ByteArray(newline) { pending[it] }
            repeat(newline + 1) { pending.removeAt(0) }
            val line = raw.toString(Charsets.UTF_8).trim()
            if (line.isNotEmpty()) lines += line
        }
        return lines
    }

    @Synchronized
    fun reset() = pending.clear()
}

