package com.leo.pixelrobot.shadow

import com.leo.pixelrobot.robot.RobotControlServer
import java.net.Socket
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Executes production route/parser with fake service callbacks and loopback bytes only. */
class PhysicalHttpApiTest {
    private var enabled = 0
    private var requested = 0
    private var cancelled = 0
    private var generation = 7L
    private var gotText = ""
    private val api get() = PhysicalHttpApi(
        status = { JSONObject().put("mode", "PHYSICAL_EXPERIMENT").put("physical_stop_confirmed", false) },
        enable = { optIn, expected -> check(optIn && expected == generation); generation++; enabled++ },
        request = { _, text, expected -> check(expected == generation); gotText = text; requested++ },
        cancel = { expected -> check(expected == generation); generation++; cancelled++ },
    )
    private fun fails(action: () -> Unit) { assertTrue(runCatching(action).isFailure) }
    private fun requestJson() = JSONObject().put("endpoint", "https://provider.invalid/v1")
        .put("model", "authored-model").put("text", "A short left step 🐕")
        .put("allow_network", true).put("expected_generation", generation)

    @Test fun distinctPhysicalEndpointsRequireExplicitCurrentOperatorIntent() {
        val a = api
        assertEquals("PHYSICAL_EXPERIMENT", a.route("GET", "/api/physical/status", "").getString("mode"))
        a.route("POST", "/api/physical/enable", """{"allow_physical":true,"expected_generation":7}""")
        assertEquals(1, enabled)
        a.route("POST", "/api/physical/request", requestJson().toString())
        assertEquals(1, requested); assertEquals("A short left step 🐕", gotText)
        a.route("POST", "/api/physical/cancel", """{"expected_generation":8}""")
        assertEquals(1, cancelled)
        fails { a.route("POST", "/api/physical/enable", """{"allow_physical":true,"expected_generation":7}""") }
    }
    @Test fun noDefaultConsentCoercionUnqualifiedBypassOrShadowGrantFields() {
        for (body in listOf("{}", """{"allow_physical":true}""", """{"allow_physical":"true","expected_generation":7}""",
            """{"allow_physical":false,"expected_generation":7}""", """{"allow_physical":true,"expected_generation":7,"allow_unqualified_metadata":true}"""))
            fails { api.route("POST", "/api/physical/enable", body) }
        for (extra in listOf("allow_unqualified_metadata", "grant", "operation_id", "bundle_digest", "lease", "arm", "start")) {
            fails { api.route("POST", "/api/physical/request", requestJson().put(extra, true).toString()) }
        }
        assertEquals(0, enabled); assertEquals(0, requested)
    }
    @Test fun exactGenerationMustBeFiniteSafeNonnegativeInteger() {
        for (value in listOf("-1", "7.1", "1e99", "\"7\"", "null", "9007199254740992", "true"))
            fails { api.route("POST", "/api/physical/cancel", "{\"expected_generation\":$value}") }
        assertEquals(0, cancelled)
    }
    @Test fun duplicateFieldsMalformedAndUnknownRoutesCannotCallService() {
        for (body in listOf("""{"allow_physical":true,"expected_generation":7,"expected_generation":7}""",
            """{"allow_physical":true,"expected_generation":7} trailing""", "[]"))
            fails { api.route("POST", "/api/physical/enable", body) }
        fails { api.route("GET", "/api/physical/enable", "") }
        fails { api.route("POST", "/api/start", "{\"expected_generation\":7}") }
        assertEquals(0, enabled)
    }
    @Test fun networkConsentAndSecureExactEndpointRequiredBeforeCallback() {
        for (body in listOf(requestJson().put("allow_network", false), requestJson().put("endpoint", "http://provider.invalid/v1"),
            requestJson().put("endpoint", "https://name:secret@provider.invalid/v1"), requestJson().put("endpoint", "https://provider.invalid/v1?q=sensitive")))
            fails { api.route("POST", "/api/physical/request", body.toString()) }
        assertEquals(0, requested)
    }
    @Test fun physicalRoutesRequireSameOriginLoopbackTokenBeforeAnyCallback() {
        RobotControlServer(null, { JSONObject() }, {}, {}, {}, {}, {}, {}, { null }, {}, {}, { null },
            pageContent = "<html>__PIXEL_SHADOW_TOKEN__</html>", port = 0, physical = api).use { server ->
            server.start()
            fun http(path: String, body: String? = null, headers: String = "", host: String = "127.0.0.1"): String =
                Socket("127.0.0.1", server.boundPort).use { socket ->
                    socket.soTimeout = 3000
                    val bytes = body?.toByteArray() ?: byteArrayOf()
                    val text = "${if (body == null) "GET" else "POST"} $path HTTP/1.1\r\nHost: $host:${server.boundPort}\r\nContent-Length: ${bytes.size}\r\n$headers\r\n"
                    socket.getOutputStream().write(text.toByteArray() + bytes)
                    socket.getInputStream().readBytes().toString(Charsets.UTF_8)
                }
            val token = Regex("<html>([0-9a-f]{64})</html>").find(http("/"))!!.groupValues[1]
            val body = """{"allow_physical":true,"expected_generation":7}"""
            assertTrue(http("/api/physical/enable", body).startsWith("HTTP/1.1 403"))
            assertTrue(http("/api/physical/enable", body, "X-Pixel-Shadow-Token: $token\r\nOrigin: https://attacker.invalid\r\n").startsWith("HTTP/1.1 403"))
            assertTrue(http("/api/physical/enable", body, "X-Pixel-Shadow-Token: $token\r\n", "rebound.invalid").startsWith("HTTP/1.1 403"))
            assertEquals(0, enabled)
            assertTrue(http("/api/physical/enable", body, "X-Pixel-Shadow-Token: $token\r\n").startsWith("HTTP/1.1 200"))
            assertEquals(1, enabled)
            assertTrue(http("/api/physical/cancel", "{\"expected_generation\":8,\"bad\":true}", "X-Pixel-Shadow-Token: $token\r\n").startsWith("HTTP/1.1 400"))
            assertEquals(0, cancelled)
        }
    }
}
