package com.leo.pixelrobot.shadow

import java.io.ByteArrayInputStream
import java.util.concurrent.atomic.AtomicInteger
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ShadowHttpApiTest {
    private fun fails(block: () -> Unit) { assertTrue(runCatching(block).isFailure) }
    @Test fun localOriginTokenAndRebindingGuards() {
        val api = ShadowHttpApi(null)
        val headers = mapOf("host" to "127.0.0.1:8767", "origin" to "http://127.0.0.1:8767", "x-pixel-shadow-token" to api.token)
        assertTrue(api.permitted(headers))
        assertFalse(api.permitted(headers - "x-pixel-shadow-token"))
        assertFalse(api.permitted(headers + ("x-pixel-shadow-token" to "bad")))
        assertFalse(api.permitted(headers + ("host" to "rebound.invalid:8767")))
        assertFalse(api.permitted(headers + ("origin" to "https://malicious.invalid")))
        assertFalse(api.permitted(headers + ("origin" to "null")))
        assertFalse(api.permitted(headers + ("sec-fetch-site" to "cross-site")))
        assertFalse(api.localHost("127.0.0.1.attacker.invalid")); assertFalse(api.localHost("localhost:99999"))
        assertTrue(api.localHost("localhost:8767")); assertTrue(api.localHost("[::1]:8767"))
    }
    @Test fun operatorRequestCannotSupplyAuthorityAndNetworkDefaultsOff() {
        val began = AtomicInteger(); val calls = AtomicInteger()
        val runner = ShadowProviderRunner(beginRequest = { _: String, _: Boolean -> began.incrementAndGet() },
            submitResponse = { _: Int, _: String, claim -> claim(); Unit }, failRequest = { _: Int -> }, cancelRequest = {}, stopPreview = {}, serviceStatus = { JSONObject() },
            callFactory = { _, _ -> calls.incrementAndGet(); object : ShadowProviderCall {
                override fun execute() = "{\"schema_version\":1,\"kind\":\"stop_request\"}"
                override fun cancel() {}
            } })
        val api = ShadowHttpApi(runner)
        val body = JSONObject().put("endpoint", "https://provider.invalid/v1").put("model", "model").put("text", "stop")
            .put("allow_network", false).put("allow_unqualified_metadata", true)
        fails { api.route("POST", "/api/shadow/request", body.toString()) }
        body.put("allow_network", true).put("capability_digest", "forged")
        fails { api.route("POST", "/api/shadow/request", body.toString()) }
        body.remove("capability_digest")
        fails { api.route("POST", "/api/shadow/request", body.toString().dropLast(1) + ",\"text\":\"other\"}") }
        assertEquals(0, began.get()); assertEquals(0, calls.get())
        assertTrue(api.route("POST", "/api/shadow/request", body.toString()).getBoolean("ok"))
        assertEquals(1, began.get()); assertEquals(1, calls.get())
        assertFalse(api.route("POST", "/api/start", "{}").getBoolean("ok"))
        runner.close()
    }
    @Test fun boundedByteParserPreservesUnicodeManualBodies() {
        val body = "{\"label\":\"停止 🐕\",\"forward\":0}"
        val bytes = body.toByteArray()
        val head = "POST /api/command HTTP/1.1\r\nHost: localhost:8767\r\nContent-Length: ${bytes.size}\r\n\r\n"
        val request = BoundedControlRequest.read(ByteArrayInputStream(head.toByteArray() + bytes))
        assertEquals(body, request.body); assertEquals("/api/command", request.path)
        assertEquals("{}", BoundedControlRequest.read(ByteArrayInputStream("POST /api/stop HTTP/1.1\r\nContent-Length: 2\r\n\r\n{}".toByteArray())).body)
    }
    @Test fun malformedTruncatedAmbiguousAndOversizedRequestsRejected() {
        for (raw in listOf("GET / HTTP/1.1\n\n", "GET / HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n",
            "POST /api/stop HTTP/1.1\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}",
            "POST /api/stop HTTP/1.1\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}",
            "POST /api/stop HTTP/1.1\r\nContent-Length: +2\r\n\r\n{}",
            "POST /api/stop HTTP/1.1\r\nContent-Length: 4\r\n\r\n{}",
            "GET / HTTP/1.1\r\nX: ${"a".repeat(9000)}\r\n\r\n",
            "POST /api/stop HTTP/1.1\r\nContent-Length: 4097\r\n\r\n"))
            fails { BoundedControlRequest.read(ByteArrayInputStream(raw.toByteArray())) }
        fails { BoundedControlRequest.read(ByteArrayInputStream("POST /api/stop HTTP/1.1\r\nContent-Length: 2\r\n\r\n".toByteArray() + byteArrayOf(0xc3.toByte(), 0x28))) }
    }
}
