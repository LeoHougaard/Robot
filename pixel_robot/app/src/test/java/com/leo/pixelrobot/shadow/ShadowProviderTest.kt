package com.leo.pixelrobot.shadow

import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread
import okhttp3.Authenticator
import okhttp3.CookieJar
import okhttp3.Protocol
import okhttp3.Response
import okhttp3.ResponseBody.Companion.toResponseBody
import okhttp3.MediaType.Companion.toMediaType
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class ShadowProviderTest {
    private val raw = """{"schema_version":1,"kind":"clarify","question":"Which direction?"}"""
    private fun envelope(content: String = raw): ByteArray = JSONObject().put("choices", JSONArray().put(JSONObject()
        .put("finish_reason", "stop").put("message", JSONObject().put("role", "assistant").put("content", content))))
        .toString().toByteArray()
    private fun config() = ShadowEndpointConfig("https://provider.invalid/v1", "explicit-model", true)
    private fun fails(block: () -> Unit) { assertTrue(runCatching(block).isFailure) }
    private fun await(check: () -> Boolean) {
        val deadline = System.nanoTime() + TimeUnit.SECONDS.toNanos(3)
        while (!check() && System.nanoTime() < deadline) Thread.sleep(2)
        assertTrue(check())
    }

    @Test fun strictCompleteEnvelopeAndRawIntentOnly() {
        assertEquals(raw, ShadowProviderProtocol.extract(envelope()))
        for (bad in listOf("{", raw + raw, "```json\n$raw\n```", "thinking $raw", raw.dropLast(1),
            """{"schema_version":1,"kind":"stop_request","kind":"stop_request"}""",
            """{"schema_version":1,"kind":"stop_request","\\u006bind":"stop_request"}""",
            """{"schema_version":1,"kind":"clarify","question":"${"x".repeat(16_385)}"}""",
            """{"schema_version":1,"kind":"stop_request","grant":true}""")) fails { ShadowProviderProtocol.extract(envelope(bad)) }
        for (bad in listOf("{}", "[]", "{\"choices\":[],\"choices\":[]}",
            """{"choices":[{"finish_reason":"length","message":{"role":"assistant","content":${JSONObject.quote(raw)}}}]}""",
            """{"choices":[{"finish_reason":"stop","message":{"role":"assistant","reasoning_content":${JSONObject.quote(raw)}}}]}""",
            """{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":${JSONObject.quote(raw)},"tool_calls":[{}]}}]}""",
            """{"choices":[{"finish_reason":"stop","message":{"role":"assistant","content":${JSONObject.quote(raw)},"refusal":"no"}}]}""",
            String(envelope()).dropLast(1), String(envelope()) + "x")) fails { ShadowProviderProtocol.extract(bad.toByteArray()) }
        fails { ShadowProviderProtocol.extract(ByteArray(65_537)) }
        fails { ShadowProviderProtocol.extract(byteArrayOf(0xc3.toByte(), 0x28)) }
    }

    @Test fun endpointConsentAndPayloadExcludePrivateHostState() {
        for (url in listOf("", "http://provider.invalid/v1", "https://u:p@provider.invalid/v1", "https://provider.invalid/v1?key=secret",
            "https://provider.invalid/v1#token", "https://provider.invalid/v2", "https://provider.invalid:0/v1", "https://provider.invalid/%76%31"))
            fails { ShadowEndpointConfig(url, "model", true) }
        fails { ShadowEndpointConfig("https://provider.invalid/v1", "model", false) }
        fails { ShadowEndpointConfig("https://provider.invalid/v1", "", true) }
        fails { ShadowProviderProtocol.payload(config(), " ") }
        fails { ShadowProviderProtocol.payload(config(), "x".repeat(2001)) }
        val payload = JSONObject(String(ShadowProviderProtocol.payload(config(), "请停止 🐕")))
        assertEquals("explicit-model", payload.getString("model"))
        assertFalse(payload.getBoolean("stream"))
        assertEquals(2, payload.getJSONArray("messages").length())
        assertEquals("请停止 🐕", payload.getJSONArray("messages").getJSONObject(1).getString("content"))
        assertEquals(setOf("model", "temperature", "max_tokens", "stream", "n", "response_format", "messages"), payload.keys().asSequence().toSet())
    }

    @Test fun cleartextIsRestrictedToLiteralLoopbackWithoutCredentials() {
        for (host in listOf("127.0.0.1", "[::1]")) {
            val local = ShadowEndpointConfig("http://$host:18081/v1", "Bonsai", true)
            assertEquals("http://$host:18081/v1/chat/completions", local.completionUrl.toString())
            fails { ShadowEndpointConfig("http://$host:18081/v1", "Bonsai", false) }
        }
        for (host in listOf("localhost", "127.0.0.2", "2130706433", "127.1", "192.168.1.2", "provider.invalid", "127.0.0.1.evil"))
            fails { ShadowEndpointConfig("http://$host:18081/v1", "Bonsai", true) }
        fails { ShadowEndpointConfig("http://user@127.0.0.1:18081/v1", "Bonsai", true) }
        fails { ShadowEndpointConfig("http://127.0.0.1:18081/v1?secret=x", "Bonsai", true) }
    }

    @Test fun isolatedClientHasNoAmbientCredentialOrRetryFeatures() {
        val client = HttpShadowProviderCall.isolatedClient
        assertSame(Authenticator.NONE, client.authenticator); assertSame(Authenticator.NONE, client.proxyAuthenticator)
        assertSame(CookieJar.NO_COOKIES, client.cookieJar); assertEquals(java.net.Proxy.NO_PROXY, client.proxy)
        assertNull(client.cache); assertFalse(client.followRedirects); assertFalse(client.followSslRedirects)
        assertFalse(client.retryOnConnectionFailure); assertEquals(8000, client.callTimeoutMillis)
        assertEquals(8000, client.writeTimeoutMillis); assertEquals(listOf(Protocol.HTTP_1_1), client.protocols)
        assertTrue(client.interceptors.isEmpty()); assertTrue(client.networkInterceptors.isEmpty())
    }

    @Test fun actualAdapterUsesOnlyInterceptedFakeResponses() {
        for ((code, type, body, expected) in listOf(
            arrayOf(200, "application/json", String(envelope()), null),
            arrayOf(302, "application/json", String(envelope()), ShadowProviderError.HTTP_ERROR),
            arrayOf(401, "application/json", "secret error body", ShadowProviderError.HTTP_ERROR),
            arrayOf(200, "text/event-stream", String(envelope()), ShadowProviderError.INVALID_CONTENT_TYPE),
            arrayOf(200, "application/json", "x".repeat(65_537), ShadowProviderError.OVERSIZED_RESPONSE))) {
            val calls = AtomicInteger()
            val client = HttpShadowProviderCall.isolatedClient.newBuilder().addInterceptor { chain ->
                calls.incrementAndGet()
                assertNull(chain.request().header("Authorization")); assertNull(chain.request().header("Cookie"))
                assertEquals("https://provider.invalid/v1/chat/completions", chain.request().url.toString())
                Response.Builder().request(chain.request()).protocol(Protocol.HTTP_1_1).code(code as Int).message("fake")
                    .header("Content-Type", type as String).body((body as String).toResponseBody(type.toMediaType())).build()
            }.build()
            val result = runCatching { HttpShadowProviderCall(config(), "stop", client).execute() }
            assertEquals(1, calls.get())
            if (expected == null) assertEquals(raw, result.getOrThrow())
            else assertEquals(expected, (result.exceptionOrNull() as ShadowProviderException).code)
        }
    }

    private class FakeCall(private val raw: String, val gate: CountDownLatch? = null, val started: CountDownLatch = CountDownLatch(1)) : ShadowProviderCall {
        val cancelled = AtomicInteger()
        override fun cancel() { cancelled.incrementAndGet() }
        override fun execute(): String {
            started.countDown()
            while (gate != null && gate.count > 0) { try { gate.await() } catch (_: InterruptedException) {} }
            return raw
        }
    }

    @Test fun cancellationTimeoutAndLateCallbacksNeverPublish() {
        for (action in listOf("cancel", "stop", "timeout", "close")) {
            val gate = CountDownLatch(1); val call = FakeCall(raw, gate); val submissions = AtomicInteger(); val failures = AtomicInteger()
            val stops = AtomicInteger(); val cancels = AtomicInteger()
            val runner = ShadowProviderRunner(beginRequest = { _: String, _: Boolean -> Any() },
                submitResponse = { _, _, claim -> if (claim()) submissions.incrementAndGet(); Unit }, failRequest = { failures.incrementAndGet(); Unit },
                cancelRequest = { cancels.incrementAndGet(); Unit }, stopPreview = { stops.incrementAndGet(); Unit },
                serviceStatus = { JSONObject() }, callFactory = { _, _ -> call }, timeoutMs = 60)
            runner.submit(config(), "hello", true); assertTrue(call.started.await(1, TimeUnit.SECONDS))
            when (action) { "cancel" -> runner.cancel(); "stop" -> runner.stop(); "close" -> runner.close(); else -> await { runner.status().getString("provider_state") == "timed_out" } }
            gate.countDown(); Thread.sleep(20)
            assertEquals(0, submissions.get()); assertTrue(call.cancelled.get() > 0)
            if (action == "timeout") await { failures.get() == 1 }
            if (action == "stop") assertEquals(1, stops.get())
            if (action == "cancel") assertEquals(1, cancels.get())
            runner.close()
        }
    }

    @Test fun blockedOldWorkCannotPublishOrStartCancelledQueuedWork() {
        val gate = CountDownLatch(1); val first = FakeCall(raw, gate); val next = FakeCall(raw); val calls = AtomicInteger(); val submits = mutableListOf<String>()
        val runner = ShadowProviderRunner(beginRequest = { text: String, _: Boolean -> text }, submitResponse = { handle, _, claim -> if (claim()) synchronized(submits) { submits.add(handle) }; Unit },
            failRequest = { _ -> }, cancelRequest = {}, stopPreview = {}, serviceStatus = { JSONObject() },
            callFactory = { _, _ -> if (calls.getAndIncrement() == 0) first else next })
        runner.submit(config(), "old", true); assertTrue(first.started.await(1, TimeUnit.SECONDS))
        runner.submit(config(), "new", true); runner.cancel(); gate.countDown(); Thread.sleep(30)
        assertEquals(1L, next.started.count); assertTrue(submits.isEmpty()); runner.close()
    }

    @Test fun revokeDuringBeginNeverStartsNetworkAndBeginsAreSerialized() {
        val entered = CountDownLatch(1); val release = CountDownLatch(1); val handle = Any(); val call = FakeCall(raw)
        lateinit var runner: ShadowProviderRunner<Any>
        runner = ShadowProviderRunner(beginRequest = { _, _ -> entered.countDown(); release.await(); handle },
            submitResponse = { _, _, _ -> fail("stale response") }, failRequest = { _ -> }, cancelRequest = {}, stopPreview = {},
            serviceStatus = { JSONObject() }, callFactory = { _, _ -> call })
        val caller = thread { runner.submit(config(), "hello", true) }
        assertTrue(entered.await(1, TimeUnit.SECONDS)); runner.cancelProvider(handle); release.countDown(); caller.join(1000)
        assertFalse(caller.isAlive); assertEquals(1L, call.started.count); assertEquals("cancelled", runner.status().getString("provider_state")); runner.close()
    }
    @Test fun timeoutAndPublicationHaveOneWinnerAtHostBoundary() {
        for (publishFirst in listOf(false, true)) {
            val entered = CountDownLatch(1); val release = CountDownLatch(1); val finished = CountDownLatch(1)
            val submits = AtomicInteger(); val failures = AtomicInteger()
            val runner = ShadowProviderRunner(beginRequest = { _: String, _: Boolean -> Any() },
                submitResponse = { _, _, claim ->
                    val early = if (publishFirst) claim() else false
                    entered.countDown()
                    while (release.count > 0) try { release.await() } catch (_: InterruptedException) {}
                    if (if (publishFirst) early else claim()) submits.incrementAndGet()
                    finished.countDown()
                }, failRequest = { failures.incrementAndGet(); Unit }, cancelRequest = {}, stopPreview = {},
                serviceStatus = { JSONObject() }, callFactory = { _, _ -> FakeCall(raw) }, timeoutMs = 60)
            try {
                runner.submit(config(), "stop", true); assertTrue(entered.await(3, TimeUnit.SECONDS))
                if (!publishFirst) await { runner.status().getString("provider_state") == "timed_out" && failures.get() == 1 }
                assertEquals(if (publishFirst) "completed" else "timed_out", runner.status().getString("provider_state"))
                release.countDown(); assertTrue(finished.await(3, TimeUnit.SECONDS))
                assertEquals(if (publishFirst) 1 else 0, submits.get())
                assertEquals(if (publishFirst) 0 else 1, failures.get())
            } finally { release.countDown(); runner.close() }
        }
    }

    @Test fun absoluteDeadlineRejectsPublicationEvenBeforeTimerRuns() {
        val clock = java.util.concurrent.atomic.AtomicLong(0)
        val entered = CountDownLatch(1); val release = CountDownLatch(1); val finished = CountDownLatch(1)
        val admitted = AtomicInteger(); val rejected = AtomicInteger()
        val runner = ShadowProviderRunner(beginRequest = { _: String, _: Boolean -> Any() },
            submitResponse = { _, _, claim ->
                entered.countDown(); release.await()
                if (claim()) admitted.incrementAndGet() else rejected.incrementAndGet()
                finished.countDown()
            }, failRequest = { _ -> }, cancelRequest = {}, stopPreview = {}, serviceStatus = { JSONObject() },
            callFactory = { _, _ -> FakeCall(raw) }, clockNanos = clock::get)
        runner.submit(config(), "stop", true); assertTrue(entered.await(1, TimeUnit.SECONDS))
        clock.set(8_000_000_000L); release.countDown(); assertTrue(finished.await(1, TimeUnit.SECONDS))
        assertEquals(0, admitted.get()); assertEquals(1, rejected.get())
        assertEquals("timed_out", runner.status().getString("provider_state")); runner.close()
    }

    @Test fun expiredQueuedRequestCannotStartTransportBeforeDelayedTimer() {
        val clock = java.util.concurrent.atomic.AtomicLong(0)
        val gate = CountDownLatch(1); val first = FakeCall(raw, gate); val second = FakeCall(raw)
        val calls = AtomicInteger(); val failed = AtomicInteger()
        val runner = ShadowProviderRunner(beginRequest = { _: String, _: Boolean -> Any() },
            submitResponse = { _, _, claim -> claim(); Unit }, failRequest = { failed.incrementAndGet(); Unit },
            cancelRequest = {}, stopPreview = {}, serviceStatus = { JSONObject() },
            callFactory = { _, _ -> if (calls.getAndIncrement() == 0) first else second }, clockNanos = clock::get)
        runner.submit(config(), "old", true); assertTrue(first.started.await(1, TimeUnit.SECONDS))
        runner.submit(config(), "new", true); clock.set(8_000_000_000L); gate.countDown()
        await { runner.status().getString("provider_state") == "timed_out" && failed.get() == 1 }
        assertEquals(1L, second.started.count); assertEquals(1, failed.get()); runner.close()
    }

}
