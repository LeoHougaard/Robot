package com.leo.pixelrobot.shadow

import java.security.MessageDigest
import java.security.SecureRandom
import org.json.JSONObject

/** Same-origin operator boundary. No CORS, serialized handle/grant, provider
 * callback URL, or action route exists. Bundle binding stays in local service.
 */
class ShadowHttpApi(private val runner: ShadowProviderRunner<*>?) {
    val token = ByteArray(32).also { SecureRandom().nextBytes(it) }.joinToString("") { "%02x".format(it.toInt() and 255) }

    fun localHost(host: String?): Boolean = host != null &&
        Regex("(127\\.0\\.0\\.1|localhost|\\[::1\\])(:[0-9]{1,5})?").matches(host.lowercase()) &&
        (host.substringAfterLast(':', "").toIntOrNull()?.let { it in 1..65535 } ?: true)

    fun permitted(headers: Map<String, String>): Boolean {
        val host = headers["host"] ?: return false
        if (!localHost(host)) return false
        if (headers["origin"] != null && headers["origin"] != "http://$host") return false
        if (headers["sec-fetch-site"] !in listOf(null, "same-origin", "none")) return false
        val supplied = headers["x-pixel-shadow-token"] ?: return false
        return MessageDigest.isEqual(token.toByteArray(Charsets.US_ASCII), supplied.toByteArray(Charsets.US_ASCII))
    }

    fun route(method: String, path: String, body: String): JSONObject {
        val selected = runner ?: return JSONObject().put("ok", false).put("error", "shadow_unavailable")
        val result = when {
            method == "GET" && path == "/api/shadow/status" -> selected.status()
            method == "POST" && path == "/api/shadow/request" -> {
                val request = StrictShadowJson.objectValue(body)
                require(request.keys == setOf("endpoint", "model", "text", "allow_network", "allow_unqualified_metadata"))
                val endpoint = request["endpoint"] as? String ?: error("endpoint")
                val model = request["model"] as? String ?: error("model")
                val text = request["text"] as? String ?: error("text")
                val network = request["allow_network"] as? Boolean ?: error("network")
                val unqualified = request["allow_unqualified_metadata"] as? Boolean ?: error("unqualified")
                selected.submit(ShadowEndpointConfig(endpoint, model, network), text, unqualified)
            }
            method == "POST" && path == "/api/shadow/cancel" -> {
                require(StrictShadowJson.objectValue(body).isEmpty()); selected.cancel()
            }
            method == "POST" && path == "/api/shadow/stop" -> {
                require(StrictShadowJson.objectValue(body).isEmpty()); selected.stop()
            }
            else -> return JSONObject().put("ok", false).put("error", "unknown_shadow_route")
        }
        return result.put("ok", true)
    }
}
