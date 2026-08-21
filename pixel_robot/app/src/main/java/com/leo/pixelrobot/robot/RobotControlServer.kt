package com.leo.pixelrobot.robot

import android.content.res.AssetManager
import org.json.JSONObject
import java.io.Closeable
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketException
import java.nio.charset.StandardCharsets
import java.util.concurrent.Executors

class RobotControlServer(
    assets: AssetManager,
    private val status: () -> JSONObject,
    private val updateCommand: (Float, Float) -> Unit,
    private val capture: () -> Unit,
    private val stand: () -> Unit,
    private val startTest: () -> Unit,
    private val stop: () -> Unit,
    private val reconnect: () -> Unit,
) : Closeable {
    private val page = assets.open("robot_control.html").bufferedReader().use { it.readText() }
    private val server = ServerSocket()
    private val workers = Executors.newCachedThreadPool()

    fun start() {
        server.reuseAddress = true
        server.bind(InetSocketAddress(InetAddress.getByName(LOOPBACK_ADDRESS), PORT))
        workers.execute {
            while (!server.isClosed) {
                try {
                    val socket = server.accept()
                    workers.execute { socket.use(::handle) }
                } catch (_: SocketException) {
                    if (!server.isClosed) close()
                }
            }
        }
    }

    private fun handle(socket: Socket) {
        socket.soTimeout = REQUEST_TIMEOUT_MS
        val reader = socket.getInputStream().bufferedReader(StandardCharsets.UTF_8)
        val requestLine = reader.readLine() ?: return
        val parts = requestLine.split(' ')
        if (parts.size != 3) {
            respond(socket, 400, JSON_CONTENT_TYPE, errorJson("invalid request line"))
            return
        }
        val method = parts[0]
        val path = parts[1].substringBefore('?')
        val headers = mutableMapOf<String, String>()
        while (true) {
            val line = reader.readLine() ?: return
            if (line.isEmpty()) break
            val separator = line.indexOf(':')
            if (separator > 0) {
                headers[line.substring(0, separator).trim().lowercase()] =
                    line.substring(separator + 1).trim()
            }
        }
        val contentLength = headers["content-length"]?.toIntOrNull() ?: 0
        if (contentLength !in 0..MAX_BODY_BYTES) {
            respond(socket, 413, JSON_CONTENT_TYPE, errorJson("request body is too large"))
            return
        }
        val body = if (contentLength == 0) {
            ""
        } else {
            CharArray(contentLength).also { chars ->
                var offset = 0
                while (offset < chars.size) {
                    val count = reader.read(chars, offset, chars.size - offset)
                    if (count < 0) error("request body ended early")
                    offset += count
                }
            }.concatToString()
        }

        try {
            when {
                method == "GET" && path == "/" -> respond(socket, 200, HTML_CONTENT_TYPE, page)
                method == "GET" && path == "/api/status" -> respond(
                    socket,
                    200,
                    JSON_CONTENT_TYPE,
                    status().put("ok", true).toString(),
                )
                method == "POST" && path == "/api/start" -> {
                    val request = JSONObject(body.ifBlank { "{}" })
                    require(request.optString("confirm") == SUSPENDED_CONFIRMATION) {
                        "confirm that the robot is securely suspended"
                    }
                    updateCommand(
                        request.getDouble("forward").toFloat(),
                        request.getDouble("yaw_rate").toFloat(),
                    )
                    startTest()
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/stand" -> {
                    val request = JSONObject(body.ifBlank { "{}" })
                    require(request.optString("confirm") == SUSPENDED_CONFIRMATION) {
                        "confirm that the robot is securely suspended"
                    }
                    stand()
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/capture" -> {
                    val request = JSONObject(body.ifBlank { "{}" })
                    require(request.optString("confirm") == SUSPENDED_CONFIRMATION) {
                        "confirm that the robot is fixed in the exact simulation start pose"
                    }
                    capture()
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/command" -> {
                    val request = JSONObject(body.ifBlank { "{}" })
                    updateCommand(
                        request.getDouble("forward").toFloat(),
                        request.getDouble("yaw_rate").toFloat(),
                    )
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/stop" -> {
                    stop()
                    respondStatus(socket)
                }
                method == "POST" && path == "/api/reconnect" -> {
                    reconnect()
                    respondStatus(socket)
                }
                else -> respond(socket, 404, JSON_CONTENT_TYPE, errorJson("not found"))
            }
        } catch (error: Throwable) {
            respond(socket, 400, JSON_CONTENT_TYPE, errorJson(error.message ?: "request failed"))
        }
    }

    private fun respondStatus(socket: Socket) {
        respond(socket, 200, JSON_CONTENT_TYPE, status().put("ok", true).toString())
    }

    private fun respond(socket: Socket, statusCode: Int, contentType: String, body: String) {
        val payload = body.toByteArray(StandardCharsets.UTF_8)
        val reason = when (statusCode) {
            200 -> "OK"
            400 -> "Bad Request"
            404 -> "Not Found"
            413 -> "Payload Too Large"
            else -> "Error"
        }
        val headers = buildString {
            append("HTTP/1.1 $statusCode $reason\r\n")
            append("Content-Type: $contentType\r\n")
            append("Content-Length: ${payload.size}\r\n")
            append("Cache-Control: no-store\r\n")
            append("X-Content-Type-Options: nosniff\r\n")
            append("Connection: close\r\n\r\n")
        }.toByteArray(StandardCharsets.US_ASCII)
        socket.getOutputStream().use { output ->
            output.write(headers)
            output.write(payload)
            output.flush()
        }
    }

    private fun errorJson(message: String): String = JSONObject()
        .put("ok", false)
        .put("error", message)
        .toString()

    override fun close() {
        runCatching { server.close() }
        workers.shutdownNow()
    }

    companion object {
        const val PORT = 8767
        const val SUSPENDED_CONFIRMATION = "ROBOT_SECURELY_SUSPENDED"
        private const val LOOPBACK_ADDRESS = "127.0.0.1"
        private const val REQUEST_TIMEOUT_MS = 3_000
        private const val MAX_BODY_BYTES = 4_096
        private const val HTML_CONTENT_TYPE = "text/html; charset=utf-8"
        private const val JSON_CONTENT_TYPE = "application/json; charset=utf-8"
    }
}
