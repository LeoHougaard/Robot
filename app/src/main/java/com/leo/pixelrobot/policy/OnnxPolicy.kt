package com.leo.pixelrobot.policy

import android.content.res.AssetManager
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import java.io.Closeable
import java.nio.FloatBuffer
import java.security.MessageDigest
import org.json.JSONObject

class OnnxPolicy(assets: AssetManager) : Closeable {
    private val environment = OrtEnvironment.getEnvironment("pixel-robot")
    private val options = OrtSession.SessionOptions().apply {
        setExecutionMode(OrtSession.SessionOptions.ExecutionMode.SEQUENTIAL)
        setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
        setInterOpNumThreads(1)
        setIntraOpNumThreads(1)
    }
    val executionProvider: String
    private val session: OrtSession

    init {
        executionProvider = try {
            options.addXnnpack(mapOf("intra_op_num_threads" to "1"))
            "XNNPACK (CPU)"
        } catch (_: Throwable) {
            "default CPU"
        }
        val model = assets.open("policy_actor.onnx").use { it.readBytes() }
        val manifest = JSONObject(
            assets.open("policy_android_manifest.json").bufferedReader().use { it.readText() },
        )
        require(manifest.getString("profile_id") == "assembly-1-12dof")
        require(model.sha256().equals(manifest.getString("onnx_sha256"), ignoreCase = true)) {
            "ONNX actor hash does not match its deployment manifest"
        }
        session = environment.createSession(model, options)
        require(session.inputNames == setOf(INPUT_NAME)) { "unexpected ONNX inputs: ${session.inputNames}" }
        require(session.outputNames == setOf(OUTPUT_NAME)) { "unexpected ONNX outputs: ${session.outputNames}" }
    }

    @Synchronized
    fun action(observation: FloatArray): FloatArray {
        require(observation.size == OBSERVATION_SIZE && observation.all(Float::isFinite))
        OnnxTensor.createTensor(environment, FloatBuffer.wrap(observation), longArrayOf(1, OBSERVATION_SIZE.toLong())).use { input ->
            session.run(mapOf(INPUT_NAME to input)).use { result ->
                val rows = result[0].value as Array<*>
                val output = rows[0] as FloatArray
                require(output.size == ACTION_SIZE && output.all(Float::isFinite))
                return output.copyOf()
            }
        }
    }

    override fun close() {
        session.close()
        options.close()
    }

    companion object {
        const val OBSERVATION_SIZE = 180
        const val ACTION_SIZE = 12
        private const val INPUT_NAME = "observation"
        private const val OUTPUT_NAME = "action"
    }
}

private fun ByteArray.sha256(): String = MessageDigest.getInstance("SHA-256")
    .digest(this)
    .joinToString("") { "%02x".format(it) }
