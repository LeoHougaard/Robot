package com.leo.pixelrobot.policy

/**
 * Independent bounded decoder for the flat intent-only JSON contract. It does
 * not use org.json, whose Android/JVM duplicate-key behavior differs. Nested
 * data, grants, host IDs, timestamps, operation lists and trailing text cannot
 * cross this boundary. This is deliberately not a general JSON parser.
 */
object ShadowIntentDecoder {
    private sealed class Scalar {
        data class Text(val value: String) : Scalar()
        data class Number(val token: String) : Scalar()
    }

    fun parse(raw: String): ShadowIntent {
        require(raw.isNotEmpty() && raw.codePointCount(0, raw.length) <= 16_384 && validUnicode(raw))
        val fields = Reader(raw).read()
        require((fields["schema_version"] as? Scalar.Number)?.token == "1")
        fun text(name: String): String = (fields[name] as? Scalar.Text)?.value ?: throw IllegalArgumentException("invalid_intent")
        fun number(name: String): Double {
            val token = (fields[name] as? Scalar.Number)?.token ?: throw IllegalArgumentException("invalid_intent")
            return requireNotNull(token.toDoubleOrNull()).also { require(it.isFinite()) }
        }
        val kind = text("kind")
        val names = when (kind) {
            "velocity_request" -> setOf("schema_version", "kind", "frame", "forward_m_s", "lateral_m_s", "yaw_rate_rad_s", "duration_ms")
            "clarify" -> setOf("schema_version", "kind", "question")
            "unsupported" -> setOf("schema_version", "kind", "reason")
            "stop_request" -> setOf("schema_version", "kind")
            else -> throw IllegalArgumentException("invalid_intent")
        }
        require(fields.keys == names)
        val intent = when (kind) {
            "velocity_request" -> {
                require(text("frame") == "body")
                val duration = (fields["duration_ms"] as? Scalar.Number)?.token ?: throw IllegalArgumentException("invalid_intent")
                require(INTEGER.matches(duration))
                ShadowIntent.Velocity(number("forward_m_s"), number("lateral_m_s"), number("yaw_rate_rad_s"),
                    requireNotNull(duration.toIntOrNull()))
            }
            "clarify" -> ShadowIntent.Clarify(text("question"))
            "unsupported" -> ShadowIntent.Unsupported(text("reason"))
            else -> ShadowIntent.Stop
        }
        require(validIntent(intent))
        return intent
    }

    private val INTEGER = Regex("-?(0|[1-9][0-9]*)")
    private val NUMBER = Regex("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?")

    private class Reader(private val raw: String) {
        private var at = 0
        private fun whitespace() { while (at < raw.length && raw[at] in " \t\r\n") at++ }
        private fun take(expected: Char) { whitespace(); require(at < raw.length && raw[at++] == expected) }

        fun read(): Map<String, Scalar> {
            take('{')
            val fields = linkedMapOf<String, Scalar>()
            whitespace()
            if (at < raw.length && raw[at] != '}') {
                while (true) {
                    val name = string()
                    require(!fields.containsKey(name))
                    take(':'); whitespace()
                    val value = if (at < raw.length && raw[at] == '"') Scalar.Text(string()) else {
                        val start = at
                        while (at < raw.length && raw[at] !in " \t\r\n,}") at++
                        val token = raw.substring(start, at)
                        require(NUMBER.matches(token))
                        Scalar.Number(token)
                    }
                    fields[name] = value
                    require(fields.size <= 8)
                    whitespace()
                    if (at >= raw.length || raw[at] != ',') break
                    at++
                }
            }
            take('}'); whitespace(); require(at == raw.length)
            return fields
        }

        private fun string(): String {
            take('"')
            val value = StringBuilder()
            while (at < raw.length) {
                val char = raw[at++]
                if (char == '"') return value.toString().also { require(validUnicode(it)) }
                require(char.code >= 0x20)
                if (char != '\\') value.append(char) else {
                    require(at < raw.length)
                    when (val escaped = raw[at++]) {
                        '"', '\\', '/' -> value.append(escaped)
                        'b' -> value.append('\b')
                        'f' -> value.append('\u000c')
                        'n' -> value.append('\n')
                        'r' -> value.append('\r')
                        't' -> value.append('\t')
                        'u' -> {
                            require(at + 4 <= raw.length)
                            val code = raw.substring(at, at + 4)
                            require(code.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' })
                            value.append(code.toInt(16).toChar()); at += 4
                        }
                        else -> throw IllegalArgumentException("invalid_intent")
                    }
                }
            }
            throw IllegalArgumentException("invalid_intent")
        }
    }
}
