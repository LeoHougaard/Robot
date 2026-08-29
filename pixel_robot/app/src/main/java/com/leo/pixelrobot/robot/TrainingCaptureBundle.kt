package com.leo.pixelrobot.robot

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.security.MessageDigest
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Builds one portable, hash-addressed package from a run and its deployed policy inputs. */
class TrainingCaptureBundle(
    private val directory: File,
    private val bundledFiles: () -> Map<String, ByteArray>,
    private val manifestContext: () -> JSONObject,
) {
    @Synchronized
    fun create(runFile: File): File {
        require(runFile.isFile && runFile.name.endsWith(".jsonl")) { "completed run data is required" }
        directory.mkdirs()
        require(directory.isDirectory) { "training capture directory is unavailable: $directory" }
        val output = File(directory, runFile.name.removeSuffix(".jsonl") + "-training-capture.zip")
        if (output.isFile && output.lastModified() >= runFile.lastModified()) return output

        val partial = File(directory, output.name + ".partial")
        partial.delete()
        val runPath = "run/${runFile.name}"
        val files = linkedMapOf<String, ByteArray>()
        bundledFiles().toSortedMap().forEach { (path, bytes) ->
            require(path.isNotBlank() && !path.startsWith('/') && ".." !in path.split('/')) {
                "unsafe training capture path: $path"
            }
            files[path] = bytes.copyOf()
        }
        requirePolicyIdentity(runFile, files)
        val fileManifest = JSONArray().put(
            JSONObject()
                .put("path", runPath)
                .put("size_bytes", runFile.length())
                .put("sha256", runFile.sha256()),
        )
        files.forEach { (path, bytes) ->
            fileManifest.put(
                JSONObject()
                    .put("path", path)
                    .put("size_bytes", bytes.size)
                    .put("sha256", bytes.sha256()),
            )
        }
        val manifest = JSONObject()
            .put("schema_version", 1)
            .put("created_unix_ms", System.currentTimeMillis())
            .put("run_entry", runPath)
            .put("context", JSONObject(manifestContext().toString()))
            .put("files", fileManifest)

        try {
            ZipOutputStream(FileOutputStream(partial).buffered()).use { zip ->
                zip.writeEntry(runPath, runFile)
                files.forEach { (path, bytes) -> zip.writeEntry(path, bytes) }
                zip.writeEntry("manifest.json", manifest.toString(2).toByteArray(Charsets.UTF_8))
            }
            if (output.exists()) require(output.delete()) { "could not replace stale training capture" }
            require(partial.renameTo(output)) { "could not finalize training capture" }
            return output
        } catch (error: Throwable) {
            partial.delete()
            throw error
        }
    }

    private fun ZipOutputStream.writeEntry(path: String, bytes: ByteArray) {
        putNextEntry(ZipEntry(path).apply { time = 0L })
        write(bytes)
        closeEntry()
    }

    private fun ZipOutputStream.writeEntry(path: String, file: File) {
        putNextEntry(ZipEntry(path).apply { time = 0L })
        file.inputStream().buffered().use { it.copyTo(this) }
        closeEntry()
    }

    private fun requirePolicyIdentity(runFile: File, files: Map<String, ByteArray>) {
        val start = runFile.bufferedReader(Charsets.UTF_8).useLines { lines ->
            lines.firstOrNull(String::isNotBlank)
        }
            ?.let(::JSONObject) ?: error("run has no session_start record")
        require(start.getString("type") == "session_start") { "run has no session_start record" }
        val context = start.getJSONObject("data").getJSONObject("context")
        val recordedManifest = context.getJSONObject("policy_android_manifest")
        val currentManifest = JSONObject(requireNotNull(files["policy/policy_android_manifest.json"]) {
            "policy Android manifest is missing"
        }.toString(Charsets.UTF_8))
        require(recordedManifest.toString() == currentManifest.toString()) {
            "run policy manifest does not match the installed actor"
        }
        val recordedMetadata = context.getJSONObject("policy_metadata")
        val currentMetadata = JSONObject(requireNotNull(files["policy/policy_metadata.json"]) {
            "policy metadata is missing"
        }.toString(Charsets.UTF_8))
        require(recordedMetadata.toString() == currentMetadata.toString()) {
            "run policy metadata does not match the installed actor"
        }
        val calibrationEntry = files.entries.single { it.key.startsWith("calibration/") }
        require(
            context.getJSONObject("calibration").toString() ==
                JSONObject(calibrationEntry.value.toString(Charsets.UTF_8)).toString(),
        ) { "run calibration does not match the installed calibration" }
        val actorHash = requireNotNull(files["policy/policy_actor.onnx"]) {
            "ONNX actor is missing"
        }.sha256()
        require(actorHash == recordedManifest.getString("onnx_sha256")) {
            "installed ONNX actor hash does not match the recorded run"
        }
    }

    private fun ByteArray.sha256(): String = MessageDigest.getInstance("SHA-256")
        .digest(this)
        .joinToString("") { "%02x".format(it) }

    private fun File.sha256(): String {
        val digest = MessageDigest.getInstance("SHA-256")
        inputStream().buffered().use { input ->
            val buffer = ByteArray(64 * 1024)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                digest.update(buffer, 0, count)
            }
        }
        return digest.digest().joinToString("") { "%02x".format(it) }
    }
}
