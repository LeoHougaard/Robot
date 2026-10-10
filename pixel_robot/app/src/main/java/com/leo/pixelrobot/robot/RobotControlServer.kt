package com.leo.pixelrobot.robot

import android.content.res.AssetManager
import com.leo.pixelrobot.policy.MotionRequest
import com.leo.pixelrobot.shadow.BoundedControlRequest
import com.leo.pixelrobot.shadow.PhysicalHttpApi
import com.leo.pixelrobot.shadow.StrictShadowJson
import com.leo.pixelrobot.shadow.ShadowHttpApi
import com.leo.pixelrobot.shadow.ShadowProviderRunner
import org.json.JSONObject
import java.io.Closeable
import java.io.File
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.nio.charset.StandardCharsets
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.RejectedExecutionException

class RobotControlServer(
    assets: AssetManager?,
    private val status: () -> JSONObject,
    private val updateCommand: (MotionRequest) -> Unit,
    private val stand: () -> Unit,
    private val startTest: (MotionRequest) -> Unit,
    private val stop: () -> Unit,
    private val reconnect: () -> Unit,
    private val selectServoTelemetry: (Int?) -> Unit,
    private val latestSession: () -> File?,
    private val startRecording: () -> Unit,
    private val stopRecording: () -> Unit,
    private val latestTrainingCapture: () -> File?,
    shadow: ShadowProviderRunner<*>? = null,
    pageContent: String? = null,
    private val port: Int = PORT,
    private val physical: PhysicalHttpApi? = null,
) : Closeable {
    private val page = pageContent ?: requireNotNull(assets).open("robot_control.html").bufferedReader().use { it.readText() }
    private val server = ServerSocket()
    private val workers = ThreadPoolExecutor(2, 8, 60, TimeUnit.SECONDS, ArrayBlockingQueue(16))
    private val shadowApi = ShadowHttpApi(shadow)
    private val manualGate = ManualOperationGate()
    internal val manualGeneration: Long get() = manualGate.generation
    fun invalidateManualOperations() { manualGate.invalidate() }
    private fun statusWithGeneration(): JSONObject = status().put("manual_generation", manualGate.generation)
    private val connections = java.util.concurrent.ConcurrentHashMap.newKeySet<Socket>()
    private val downloadConnections = java.util.concurrent.ConcurrentHashMap.newKeySet<Socket>()
    private val downloads = ThreadPoolExecutor(1, 2, 30, TimeUnit.SECONDS, ArrayBlockingQueue(2))
    private val expiry = java.util.concurrent.ScheduledThreadPoolExecutor(1).apply { removeOnCancelPolicy = true }
    private class ConnectionLease(var future: java.util.concurrent.ScheduledFuture<*>? = null)
    private val lifetimes = java.util.concurrent.ConcurrentHashMap<Socket, ConnectionLease>()
    val boundPort: Int get() = server.localPort

    fun start() {
        server.reuseAddress = true
        server.bind(InetSocketAddress(InetAddress.getByName(LOOPBACK_ADDRESS), port))
        workers.execute {
            while (!server.isClosed) {
                try {
                    val socket = server.accept()
                    val deadline = System.nanoTime() + REQUEST_TIMEOUT_MS * 1_000_000L
                    connections.add(socket)
                    if (server.isClosed) { closeConnection(socket); continue }
                    try {
                        scheduleLifetime(socket, REQUEST_TIMEOUT_MS.toLong())
                        workers.execute { try { runCatching { handle(socket, deadline) } } finally { if (!downloadConnections.contains(socket)) closeConnection(socket) } }
                    } catch (_: RejectedExecutionException) { closeConnection(socket) }
                } catch (_: SocketException) {
                    if (!server.isClosed) close()
                }
            }
        }
    }

    private fun handle(socket: Socket, deadline: Long) {
        socket.soTimeout = REQUEST_TIMEOUT_MS
        val input = object : java.io.FilterInputStream(socket.getInputStream()) {
            private fun remaining() {
                val nanos = deadline - System.nanoTime()
                if (nanos <= 0) throw java.net.SocketTimeoutException()
                socket.soTimeout = (nanos / 1_000_000L).coerceIn(1, REQUEST_TIMEOUT_MS.toLong()).toInt()
            }
            override fun read(): Int { remaining(); return super.read() }
            override fun read(bytes: ByteArray, offset: Int, length: Int): Int { remaining(); return `in`.read(bytes, offset, length) }
        }
        val request = try { BoundedControlRequest.read(input) } catch (_: Exception) {
            respond(socket, 400, JSON_CONTENT_TYPE, errorJson("invalid_or_oversized_request"))
            return
        }
        if (System.nanoTime() >= deadline) {
            respond(socket, 400, JSON_CONTENT_TYPE, errorJson("request_expired")); return
        }
        val method = request.method
        val path = request.path
        val headers = request.headers
        val body = request.body
        if (path.startsWith("/api/physical/")) {
            if (!shadowApi.permitted(headers)) {
                respond(socket, 403, JSON_CONTENT_TYPE, errorJson("local_operator_token_required")); return
            }
            val result = try { physical?.route(method, path, body)
                ?: JSONObject().put("ok", false).put("error", "physical_unavailable")
            } catch (_: Exception) {
                physical?.rejected() ?: JSONObject().put("ok", false).put("error", "physical_unavailable")
            }
            respond(socket, if (result.optBoolean("ok")) 200 else 400, JSON_CONTENT_TYPE, result.toString())
            return
        }
        if (path.startsWith("/api/shadow/")) {
            if (!shadowApi.permitted(headers)) {
                respond(socket, 403, JSON_CONTENT_TYPE, errorJson("local_operator_token_required"))
                return
            }
            val result = try { shadowApi.route(method, path, body) } catch (_: Exception) {
                JSONObject().put("ok", false).put("error", "invalid_shadow_request")
            }
            respond(socket, if (result.optBoolean("ok")) 200 else 400, JSON_CONTENT_TYPE, result.toString())
            return
        }

        // Every mutation, including legacy manual motor/configuration routes, uses
        // the same loopback browser fence. A physical endpoint must never be a CSRF bypass.
        if (method == "POST" && !shadowApi.permitted(headers)) {
            respond(socket, 403, JSON_CONTENT_TYPE, errorJson("local_operator_token_required"))
            return
        }
        try {
            when {
                method == "GET" && path == "/" -> respond(socket, 200, HTML_CONTENT_TYPE,
                    page.replace("__PIXEL_SHADOW_TOKEN__", if (shadowApi.localHost(headers["host"])) shadowApi.token else ""))
                method == "GET" && path == "/api/status" -> respond(
                    socket,
                    200,
                    JSON_CONTENT_TYPE,
                    statusWithGeneration().put("ok", true).toString(),
                )
                method == "GET" && path == "/api/session/latest" -> {
                    val file = latestSession()
                    if (file == null || !file.isFile) {
                        respond(socket, 404, JSON_CONTENT_TYPE, errorJson("no completed run session"))
                    } else {
                        queueFile(socket, file)
                    }
                }
                method == "GET" && path == "/api/session/latest-training-capture" -> {
                    val file = latestTrainingCapture()
                    if (file == null || !file.isFile) {
                        respond(socket, 404, JSON_CONTENT_TYPE, errorJson("no completed training capture"))
                    } else {
                        queueFile(socket, file, "application/zip")
                    }
                }
                method == "POST" && path == "/api/record/start" -> {
                    startRecording()
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/record/stop" -> {
                    stopRecording()
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/start" -> {
                    val expected = expectedManualGeneration(body)
                    val request = motionRequest(JSONObject(body))
                    manualGate.admit(expected, { System.nanoTime() < deadline }) { startTest(request) }
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/stand" -> {
                    val expected = expectedManualGeneration(body)
                    manualGate.admit(expected, { System.nanoTime() < deadline }) { stand() }
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/command" -> {
                    val request = JSONObject(body.ifBlank { "{}" })
                    updateCommand(motionRequest(request))
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/stop" -> {
                    manualGate.invalidate()
                    stop()
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/reconnect" -> {
                    manualGate.invalidate()
                    reconnect()
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/servo-telemetry" -> {
                    val request = JSONObject(body.ifBlank { "{}" })
                    selectServoTelemetry(
                        if (!request.has("id") || request.isNull("id")) null else request.getInt("id"),
                    )
                    respondStatus(socket)
                }
                else -> respond(socket, 404, JSON_CONTENT_TYPE, errorJson("not found"))
            }
        } catch (error: Throwable) {
            respond(socket, 400, JSON_CONTENT_TYPE, JSONObject().put("ok", false)
                .put("error", error.message ?: "request failed").put("manual_generation", manualGate.generation).toString())
        }
    }

    private fun expectedManualGeneration(body: String): Long {
        val value = StrictShadowJson.objectValue(body)["expected_manual_generation"] as? Double
            ?: error("expected_manual_generation_required")
        require(value >= 0 && value <= 9_007_199_254_740_991.0 && value == value.toLong().toDouble())
        return value.toLong()
    }

    private fun motionRequest(value: JSONObject) = MotionRequest(
        forward = value.getDouble("forward").toFloat(),
        yawRate = value.getDouble("yaw_rate").toFloat(),
        lateral = value.optDouble("lateral", 0.0).toFloat(),
        heightOffset = value.optDouble("height_offset", 0.0).toFloat(),
        roll = value.optDouble("roll", 0.0).toFloat(),
        pitch = value.optDouble("pitch", 0.0).toFloat(),
    )

    private fun respondStatus(socket: Socket) {
        respond(socket, 200, JSON_CONTENT_TYPE, statusWithGeneration().put("ok", true).toString())
    }

    private fun respond(socket: Socket, statusCode: Int, contentType: String, body: String) {
        val payload = body.toByteArray(StandardCharsets.UTF_8)
        val reason = when (statusCode) {
            200 -> "OK"
            400 -> "Bad Request"
            403 -> "Forbidden"
            404 -> "Not Found"
            413 -> "Payload Too Large"
            503 -> "Service Unavailable"
            else -> "Error"
        }
        val headers = buildString {
            append("HTTP/1.1 $statusCode $reason\r\n")
            append("Content-Type: $contentType\r\n")
            append("Content-Length: ${payload.size}\r\n")
            append("Cache-Control: no-store\r\n")
            append("X-Content-Type-Options: nosniff\r\n")
            append("X-Frame-Options: DENY\r\n")
            append("Content-Security-Policy: frame-ancestors 'none'\r\n")
            append("Connection: close\r\n\r\n")
        }.toByteArray(StandardCharsets.US_ASCII)
        socket.getOutputStream().use { output ->
            output.write(headers)
            output.write(payload)
            output.flush()
        }
    }

    private fun queueFile(socket: Socket, file: File, contentType: String = "application/x-ndjson") {
        // Downloads never occupy the control pool while a peer stops reading.
        // A separate lifetime bounds queued and in-progress file responses.
        try {
            synchronized(socket) {
                if (socket.isClosed || server.isClosed) return
                downloadConnections.add(socket)
                scheduleLifetime(socket, 30_000)
            }
            downloads.execute { try { respondFile(socket, file, contentType) } catch (_: Exception) {} finally { closeConnection(socket) } }
        } catch (_: RejectedExecutionException) {
            downloadConnections.remove(socket)
            respond(socket, 503, JSON_CONTENT_TYPE, errorJson("download_capacity_busy"))
        }
    }

    private fun scheduleLifetime(socket: Socket, delayMs: Long) = synchronized(socket) {
        if (socket.isClosed || server.isClosed) throw RejectedExecutionException()
        lifetimes.remove(socket)?.future?.cancel(false)
        val lease = ConnectionLease()
        lifetimes[socket] = lease
        lease.future = expiry.schedule({ synchronized(socket) {
            if (lifetimes[socket] === lease) closeConnection(socket)
        } }, delayMs, TimeUnit.MILLISECONDS)
    }

    private fun closeConnection(socket: Socket) = synchronized(socket) {
        connections.remove(socket); downloadConnections.remove(socket)
        lifetimes.remove(socket)?.future?.cancel(false)
        runCatching { socket.close() }
        Unit
    }

    private fun respondFile(socket: Socket, file: File, contentType: String = "application/x-ndjson") {
        val headers = buildString {
            append("HTTP/1.1 200 OK\r\n")
            append("Content-Type: $contentType\r\n")
            append("Content-Length: ${file.length()}\r\n")
            append("Content-Disposition: attachment; filename=\"${file.name}\"\r\n")
            append("Cache-Control: no-store\r\n")
            append("X-Content-Type-Options: nosniff\r\n")
            append("X-Frame-Options: DENY\r\n")
            append("Content-Security-Policy: frame-ancestors 'none'\r\n")
            append("Connection: close\r\n\r\n")
        }.toByteArray(StandardCharsets.US_ASCII)
        socket.getOutputStream().use { output ->
            output.write(headers)
            file.inputStream().use { input -> input.copyTo(output) }
            output.flush()
        }
    }

    private fun errorJson(message: String): String = JSONObject()
        .put("ok", false)
        .put("error", message)
        .toString()

    override fun close() {
        manualGate.invalidate()
        runCatching { server.close() }
        connections.forEach { closeConnection(it) }
        connections.clear()
        workers.shutdownNow(); downloads.shutdownNow(); expiry.shutdownNow()
    }

    companion object {
        const val PORT = 8767
        private const val LOOPBACK_ADDRESS = "127.0.0.1"
        private const val REQUEST_TIMEOUT_MS = 3_000
        private const val HTML_CONTENT_TYPE = "text/html; charset=utf-8"
        private const val JSON_CONTENT_TYPE = "application/json; charset=utf-8"
    }
}
