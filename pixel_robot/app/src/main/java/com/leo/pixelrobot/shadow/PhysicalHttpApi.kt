package com.leo.pixelrobot.shadow

import org.json.JSONObject

/** Distinct operator physical endpoints. Provider JSON cannot reach these callbacks. */
class PhysicalHttpApi(
    private val status: () -> JSONObject,
    private val enable: (Boolean, Long) -> Unit,
    private val request: (ShadowEndpointConfig, String, Long) -> Unit,
    private val cancel: (Long) -> Unit,
) {
    fun rejected(): JSONObject = status().put("ok", false).put("error", "physical_request_rejected_refresh_status")
    fun route(method: String, path: String, body: String): JSONObject {
        if (method == "GET" && path == "/api/physical/status") return status().put("ok", true)
        require(method == "POST")
        val fields = StrictShadowJson.objectValue(body)
        val number = fields["expected_generation"] as? Double ?: error("operator_generation_required")
        require(number >= 0 && number <= 9_007_199_254_740_991.0 && number == number.toLong().toDouble())
        val generation = number.toLong()
        when (path) {
            "/api/physical/enable" -> {
                require(fields.keys == setOf("allow_physical", "expected_generation"))
                enable(fields["allow_physical"] as? Boolean ?: error("physical_opt_in_required"), generation)
            }
            "/api/physical/request" -> {
                require(fields.keys == setOf("endpoint", "model", "text", "allow_network", "expected_generation"))
                val config = ShadowEndpointConfig(fields["endpoint"] as? String ?: error("endpoint"),
                    fields["model"] as? String ?: error("model"), fields["allow_network"] as? Boolean ?: error("network"))
                request(config, fields["text"] as? String ?: error("text"), generation)
            }
            "/api/physical/cancel" -> {
                require(fields.keys == setOf("expected_generation")); cancel(generation)
            }
            else -> error("unknown_physical_route")
        }
        return status().put("ok", true)
    }
}
