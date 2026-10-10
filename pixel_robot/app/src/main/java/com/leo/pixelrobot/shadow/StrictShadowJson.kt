package com.leo.pixelrobot.shadow

/** Small bounded JSON reader: duplicates (including escaped keys), invalid Unicode,
 * lenient syntax, non-finite numbers, deep nesting and trailing data fail closed.
 * Used for the provider envelope and local operator request, never to repair intents.
 */
internal object StrictShadowJson {
    fun objectValue(raw: String): Map<String, Any?> {
        require(raw.length <= 65_536 && unicode(raw))
        val reader = Reader(raw)
        val value = reader.value(0)
        reader.end()
        @Suppress("UNCHECKED_CAST")
        return value as? Map<String, Any?> ?: error("invalid_json_object")
    }

    fun unicode(value: String): Boolean {
        var i = 0
        while (i < value.length) {
            val c = value[i++]
            if (c.isHighSurrogate()) {
                if (i >= value.length || !value[i++].isLowSurrogate()) return false
            } else if (c.isLowSurrogate()) return false
        }
        return true
    }

    private class Reader(val raw: String) {
        var at = 0
        var nodes = 0
        fun ws() { while (at < raw.length && raw[at] in " \r\n\t") at++ }
        fun end() { ws(); require(at == raw.length) }
        fun take(c: Char) { ws(); require(at < raw.length && raw[at++] == c) }
        fun value(depth: Int): Any? {
            require(depth <= 16 && ++nodes <= 4096); ws(); require(at < raw.length)
            return when (raw[at]) {
                '{' -> {
                    at++; ws(); val out = linkedMapOf<String, Any?>()
                    if (at < raw.length && raw[at] != '}') while (true) {
                        val key = string(); require(!out.containsKey(key)); take(':')
                        out[key] = value(depth + 1); ws()
                        if (at >= raw.length || raw[at] != ',') break
                        at++
                    }
                    take('}'); out
                }
                '[' -> {
                    at++; ws(); val out = mutableListOf<Any?>()
                    if (at < raw.length && raw[at] != ']') while (true) {
                        out.add(value(depth + 1)); ws()
                        if (at >= raw.length || raw[at] != ',') break
                        at++
                    }
                    take(']'); out
                }
                '"' -> string()
                't' -> literal("true", true)
                'f' -> literal("false", false)
                'n' -> literal("null", null)
                else -> {
                    val start = at
                    while (at < raw.length && raw[at] !in " \r\n\t,}]") at++
                    val number = raw.substring(start, at)
                    require(Regex("-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][+-]?[0-9]+)?").matches(number))
                    number.toDouble().also { require(it.isFinite()) }
                }
            }
        }
        fun literal(word: String, value: Any?): Any? {
            require(raw.startsWith(word, at)); at += word.length; return value
        }
        fun string(): String {
            take('"'); val out = StringBuilder()
            while (at < raw.length) {
                val c = raw[at++]
                if (c == '"') return out.toString().also { require(unicode(it)) }
                require(c.code >= 32)
                if (c != '\\') out.append(c) else {
                    require(at < raw.length)
                    when (val e = raw[at++]) {
                        '"', '\\', '/' -> out.append(e)
                        'b' -> out.append('\b')
                        'f' -> out.append('\u000c')
                        'n' -> out.append('\n')
                        'r' -> out.append('\r')
                        't' -> out.append('\t')
                        'u' -> {
                            require(at + 4 <= raw.length)
                            val hex = raw.substring(at, at + 4)
                            require(hex.all { it in '0'..'9' || it in 'a'..'f' || it in 'A'..'F' })
                            out.append(hex.toInt(16).toChar()); at += 4
                        }
                        else -> error("invalid_json_escape")
                    }
                }
            }
            error("unterminated_json_string")
        }
    }
}
