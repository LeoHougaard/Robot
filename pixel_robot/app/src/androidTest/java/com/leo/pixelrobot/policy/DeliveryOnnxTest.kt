package com.leo.pixelrobot.policy

import android.os.SystemClock
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.io.File
import kotlin.math.abs
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class DeliveryOnnxTest {
    @Test
    fun deliveryShapeMatchesReferenceAtFiftyHz() {
        // Test-APK assets only. The installed app's trained actor is untouched.
        val assets = InstrumentationRegistry.getInstrumentation().context.assets
        fun read(name: String) = JSONObject(assets.open(name).bufferedReader().use { it.readText() })
        val manifest = read("policy_android_manifest.json")
        val reference = read("policy_reference.json")
        val cases = reference.getJSONArray("cases")
        val observations = (0 until cases.length()).map { index ->
            val values = cases.getJSONObject(index).getJSONArray("observation")
            FloatArray(values.length()) { values.getDouble(it).toFloat() }
        }
        val times = mutableListOf<Double>()
        OnnxPolicy(assets, "test-only-delivery-shape", manifest.getString("profile_sha256"),
            manifest.getString("source_weights_sha256"), 426).use { policy ->
            var deadline = SystemClock.elapsedRealtimeNanos()
            repeat(550) { frame ->
                val index = frame % observations.size
                val start = SystemClock.elapsedRealtimeNanos()
                val actual = policy.action(observations[index])
                val elapsed = (SystemClock.elapsedRealtimeNanos() - start) / 1_000_000.0
                val expected = cases.getJSONObject(index).getJSONArray("action")
                actual.indices.forEach { joint ->
                    val target = expected.getDouble(joint)
                    assertTrue("frame $frame joint $joint parity", abs(actual[joint] - target) <=
                        reference.getDouble("atol") + reference.getDouble("rtol") * abs(target))
                }
                if (frame >= 50) times += elapsed
                deadline += 20_000_000L
                val remaining = deadline - SystemClock.elapsedRealtimeNanos()
                if (remaining > 0) Thread.sleep(remaining / 1_000_000L, (remaining % 1_000_000L).toInt())
                else deadline = SystemClock.elapsedRealtimeNanos()
            }
            val sorted = times.sorted()
            val p99 = sorted[(sorted.size * .99).toInt() - 1]
            val report = JSONObject().put("fixture_only", true).put("observation_size", 426)
                .put("cases", observations.size).put("measured_inferences", times.size)
                .put("scheduled_hz", 50).put("provider", policy.executionProvider)
                .put("median_ms", sorted[sorted.size / 2]).put("p99_ms", p99)
                .put("max_ms", sorted.last()).put("onnx_sha256", manifest.getString("onnx_sha256"))
            val context = ApplicationProvider.getApplicationContext<android.content.Context>()
            File(context.filesDir, "delivery-onnx-fixture-result.json").writeText(report.toString(2))
            assertTrue("426-input inference p99 $p99 ms must leave room in a 20 ms control period", p99 < 10.0)
        }
    }
}
