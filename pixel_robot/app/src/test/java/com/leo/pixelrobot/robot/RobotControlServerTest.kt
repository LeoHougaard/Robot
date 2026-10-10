package com.leo.pixelrobot.robot

import java.net.Socket
import java.util.concurrent.atomic.AtomicInteger
import com.leo.pixelrobot.shadow.ShadowProviderRunner
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

/** Isolated loopback fake callbacks only. No Android assets, robot or provider endpoint. */
class RobotControlServerTest {
    private fun server(stops: AtomicInteger = AtomicInteger(), shadow: ShadowProviderRunner<*>? = null, blockStand: () -> Unit = {}, latest: () -> java.io.File? = { null }) = RobotControlServer(
        null, { JSONObject().put("source", "fake") }, {}, blockStand, {}, { stops.incrementAndGet(); Unit }, {}, {}, latest, {}, {}, { null },
        shadow, "<html>__PIXEL_SHADOW_TOKEN__</html>", 0).also { it.start() }
    private fun request(server: RobotControlServer, path: String, body: String? = null, more: String = "", host: String = "127.0.0.1"): String =
        Socket("127.0.0.1", server.boundPort).use { socket ->
            socket.soTimeout = 5000
            val bytes = body?.toByteArray() ?: byteArrayOf()
            val head = "${if (body == null) "GET" else "POST"} $path HTTP/1.1\r\nHost: $host:${server.boundPort}\r\nContent-Length: ${bytes.size}\r\n$more\r\n"
            socket.getOutputStream().write(head.toByteArray() + bytes)
            socket.getInputStream().readBytes().toString(Charsets.UTF_8)
        }
    @Test fun realLoopbackRoutesPreserveManualStopAndDenyShadowCsrf() {
        val stops = AtomicInteger()
        server(stops).use { server ->
            val root = request(server, "/")
            assertTrue(root.contains("X-Frame-Options: DENY")); assertTrue(root.contains("frame-ancestors 'none'"))
            val token = Regex("<html>([0-9a-f]{64})</html>").find(root)!!.groupValues[1]
            assertTrue(request(server, "/", host = "rebound.invalid").endsWith("<html></html>"))
            assertTrue(request(server, "/api/shadow/request", "{}").startsWith("HTTP/1.1 403"))
            assertTrue(request(server, "/api/shadow/status", more = "X-Pixel-Shadow-Token: $token\r\nOrigin: https://attacker.invalid\r\n").startsWith("HTTP/1.1 403"))
            assertTrue(request(server, "/api/stop", "{}").startsWith("HTTP/1.1 200")); assertEquals(1, stops.get())
            assertTrue(request(server, "/api/status").contains("\"source\":\"fake\""))
        }
    }
    @Test fun closeReleasesAcceptedAndQueuedSockets() {
        val server = server()
        val sockets = (1..18).map { Socket("127.0.0.1", server.boundPort).apply { soTimeout = 2000 } }
        Thread.sleep(50); server.close()
        for (socket in sockets) socket.use {
            val result = runCatching { it.getInputStream().read() }
            assertTrue(result.getOrNull() == -1 || result.exceptionOrNull() is java.net.SocketException)
        }
    }
    @Test fun queueAgeCountsAgainstOriginalRequestDeadline() {
        val entered = java.util.concurrent.CountDownLatch(1)
        val release = java.util.concurrent.CountDownLatch(1)
        val stops = AtomicInteger()
        server(stops, blockStand = { entered.countDown(); release.await() }).use { server ->
            val first = Thread { request(server, "/api/stand", "{}") }.apply { start() }
            assertTrue(entered.await(1, java.util.concurrent.TimeUnit.SECONDS))
            var result = ""
            val queued = Thread { result = request(server, "/api/stop", "{}") }.apply { start() }
            Thread.sleep(3200); release.countDown(); queued.join(2000); first.join(2000)
            assertFalse(queued.isAlive); assertTrue(result.isEmpty() || result.startsWith("HTTP/1.1 400")); assertEquals(0, stops.get())
        }
    }
    @Test fun stalledSyntheticDownloadsDoNotOccupyControlWorkers() {
        val file = java.io.File.createTempFile("pixel-shadow-synthetic", ".jsonl")
        java.io.RandomAccessFile(file, "rw").use { it.setLength(32L * 1024 * 1024) }
        val stops = AtomicInteger()
        try {
            server(stops, latest = { file }).use { server ->
                val peers = (1..6).map {
                    Socket("127.0.0.1", server.boundPort).apply {
                        receiveBufferSize = 1024
                        getOutputStream().write("GET /api/session/latest HTTP/1.1\r\nHost: localhost\r\n\r\n".toByteArray())
                    }
                }
                try {
                    val start = System.nanoTime()
                    assertTrue(request(server, "/api/stop", "{}").startsWith("HTTP/1.1 200"))
                    assertEquals(1, stops.get()); assertTrue((System.nanoTime() - start) / 1_000_000 < 2000)
                } finally { peers.forEach { it.close() } }
            }
        } finally { file.delete() }
    }
    @Test fun wholeRequestDeadlineDefeatsSlowByteDrip() {
        server().use { server ->
            Socket("127.0.0.1", server.boundPort).use { socket ->
                socket.soTimeout = 4500
                val writer = Thread {
                    for (byte in "GET / HTTP/1.1\r\nHost: localhost\r\n\r\n".toByteArray()) {
                        if (runCatching { socket.getOutputStream().write(byte.toInt()); Thread.sleep(200) }.isFailure) break
                    }
                }.apply { isDaemon = true; start() }
                val start = System.nanoTime()
                val reply = socket.getInputStream().readBytes().toString(Charsets.UTF_8)
                assertTrue(reply.isEmpty() || reply.startsWith("HTTP/1.1 400"))
                assertTrue((System.nanoTime() - start) / 1_000_000 < 4300)
                writer.interrupt(); writer.join(500)
            }
        }
    }
}
