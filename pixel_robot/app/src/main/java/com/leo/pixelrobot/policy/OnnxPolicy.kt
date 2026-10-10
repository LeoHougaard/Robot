package com.leo.pixelrobot.policy

import android.content.res.AssetManager
import ai.onnxruntime.OnnxTensor
import ai.onnxruntime.OnnxJavaType
import ai.onnxruntime.OrtEnvironment
import ai.onnxruntime.OrtSession
import ai.onnxruntime.TensorInfo
import java.io.Closeable
import java.nio.FloatBuffer
import java.security.MessageDigest
import org.json.JSONObject

class OnnxPolicy(
    assets: AssetManager,
    expectedProfileId: String,
    expectedProfileSha256: String,
    expectedWeightsSha256: String,
    private val observationSize: Int,
    private val assetPrefix: String = "",
    loadedAssets: LoadedPolicyAssets? = null,
) : Closeable {
    private val environment = OrtEnvironment.getEnvironment("pixel-robot")
    private val options = OrtSession.SessionOptions()
    val executionProvider: String
    private val session: OrtSession

    init {
        var createdSession: OrtSession? = null
        try {
            options.apply {
                setExecutionMode(OrtSession.SessionOptions.ExecutionMode.SEQUENTIAL)
                setOptimizationLevel(OrtSession.SessionOptions.OptLevel.ALL_OPT)
                setInterOpNumThreads(1)
                setIntraOpNumThreads(1)
            }
            executionProvider = try {
                options.addXnnpack(mapOf("intra_op_num_threads" to "1"))
                "XNNPACK (CPU)"
            } catch (_: Throwable) {
                "default CPU"
            }
            fun asset(name: String) = if (assetPrefix.isEmpty()) name else assetPrefix + "/" + name
            require(loadedAssets == null || assetPrefix.isEmpty()) { "A loaded bundle cannot select another asset prefix" }
            val model = loadedAssets?.actor() ?: assets.open(asset("policy_actor.onnx")).use { it.readBytes() }
            val manifest = JSONObject(loadedAssets?.manifest()?.toString(Charsets.UTF_8)
                ?: assets.open(asset("policy_android_manifest.json")).bufferedReader().use { it.readText() })
            require(manifest.getString("profile_id") == expectedProfileId) {
                "ONNX actor profile does not match policy metadata"
            }
            require(manifest.getString("profile_sha256").equals(expectedProfileSha256, ignoreCase = true)) {
                "ONNX actor profile hash does not match policy metadata"
            }
            require(manifest.getString("source_weights_sha256").equals(expectedWeightsSha256, ignoreCase = true)) {
                "ONNX source weights do not match policy metadata"
            }
            require(model.sha256().equals(manifest.getString("onnx_sha256"), ignoreCase = true)) {
                "ONNX actor hash does not match its deployment manifest"
            }
            session = environment.createSession(model, options).also { createdSession = it }
            require(session.inputNames == setOf(INPUT_NAME)) { "unexpected ONNX inputs: ${session.inputNames}" }
            require(session.outputNames == setOf(OUTPUT_NAME)) { "unexpected ONNX outputs: ${session.outputNames}" }
            val inputInfo = session.inputInfo.getValue(INPUT_NAME).info as TensorInfo
            val inputShape = inputInfo.shape
            require(inputInfo.type == OnnxJavaType.FLOAT) { "ONNX observations must be float32" }
            require(inputShape.contentEquals(longArrayOf(1, observationSize.toLong()))) {
                "ONNX observation shape ${inputShape.contentToString()} does not match metadata"
            }
            val outputInfo = session.outputInfo.getValue(OUTPUT_NAME).info as TensorInfo
            require(outputInfo.type == OnnxJavaType.FLOAT &&
                outputInfo.shape.contentEquals(longArrayOf(1, ACTION_SIZE.toLong()))) {
                "ONNX action output must be float32 [1, $ACTION_SIZE]"
            }
        } catch (error: Throwable) {
            // Constructors can fail before the controller owns this object.
            // Release native resources on every manifest/model-shape failure.
            runCatching { createdSession?.close() }
            runCatching { options.close() }
            throw error
        }
    }

    @Synchronized
    fun action(observation: FloatArray): FloatArray {
        require(observation.size == observationSize && observation.all(Float::isFinite))
        OnnxTensor.createTensor(environment, FloatBuffer.wrap(observation), longArrayOf(1, observationSize.toLong())).use { input ->
            session.run(mapOf(INPUT_NAME to input)).use { result ->
                val rows = result[0].value as Array<*>
                val output = rows[0] as FloatArray
                require(output.size == ACTION_SIZE && output.all(Float::isFinite))
                return output.copyOf()
            }
        }
    }

    @Synchronized
    override fun close() {
        session.close()
        options.close()
    }

    companion object {
        const val ACTION_SIZE = 12
        private const val INPUT_NAME = "observation"
        private const val OUTPUT_NAME = "action"
    }
}

private fun ByteArray.sha256(): String = MessageDigest.getInstance("SHA-256")
    .digest(this)
    .joinToString("") { "%02x".format(it) }
