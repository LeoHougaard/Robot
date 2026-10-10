package com.leo.pixelrobot.shadow

import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.nio.ByteBuffer
import java.nio.charset.CodingErrorAction

/** Byte-based Content-Length, bounded ASCII headers and strict UTF-8 body.
 * Reject ambiguity before any route (manual or shadow) can change state.
 */
internal data class BoundedControlRequest(val method: String, val path: String,
    val headers: Map<String, String>, val body: String) {
    companion object {
        fun read(input: InputStream): BoundedControlRequest {
            var headerBytes = 0
            fun line(): String {
                val bytes = ByteArrayOutputStream()
                while (true) {
                    val b = input.read()
                    require(b >= 0 && ++headerBytes <= 8192 && bytes.size() <= 4096)
                    if (b == 13) { require(input.read() == 10 && ++headerBytes <= 8192); break }
                    require(b == 9 || b in 32..126)
                    bytes.write(b)
                }
                return bytes.toString("US-ASCII")
            }
            val first = line().split(' ')
            require(first.size == 3 && first[0] in setOf("GET", "POST", "OPTIONS") &&
                first[1].startsWith('/') && !first[1].startsWith("//") && first[2] in setOf("HTTP/1.1", "HTTP/1.0"))
            val headers = linkedMapOf<String, String>()
            while (true) {
                val header = line()
                if (header.isEmpty()) break
                val at = header.indexOf(':')
                require(at > 0)
                val name = header.substring(0, at).lowercase()
                require(Regex("[a-z0-9-]+").matches(name) && !headers.containsKey(name))
                headers[name] = header.substring(at + 1).trim()
            }
            require(!headers.containsKey("transfer-encoding"))
            val length = headers["content-length"]?.let {
                require(Regex("0|[1-9][0-9]*").matches(it)); it.toInt()
            } ?: 0
            val path = first[1].substringBefore('?')
            require(length in 0..if (path.startsWith("/api/shadow/")) 16_384 else 4096)
            val bytes = ByteArray(length)
            var offset = 0
            while (offset < length) {
                val count = input.read(bytes, offset, length - offset)
                require(count > 0); offset += count
            }
            val decoder = Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT).onUnmappableCharacter(CodingErrorAction.REPORT)
            return BoundedControlRequest(first[0], path, headers, decoder.decode(ByteBuffer.wrap(bytes)).toString())
        }
    }
}
