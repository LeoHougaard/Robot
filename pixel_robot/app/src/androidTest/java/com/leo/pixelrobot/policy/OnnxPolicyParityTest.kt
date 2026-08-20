package com.leo.pixelrobot.policy

import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import kotlin.math.abs
import org.json.JSONObject
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class OnnxPolicyParityTest {
    @Test
    fun actorMatchesDesktopReferenceVectors() {
        val context = ApplicationProvider.getApplicationContext<android.content.Context>()
        val reference = JSONObject(
            context.assets.open("policy_reference.json").bufferedReader().use { it.readText() },
        )
        val absoluteTolerance = reference.getDouble("atol")
        val relativeTolerance = reference.getDouble("rtol")
        OnnxPolicy(context.assets).use { policy ->
            val cases = reference.getJSONArray("cases")
            for (caseIndex in 0 until cases.length()) {
                val case = cases.getJSONObject(caseIndex)
                val observationJson = case.getJSONArray("observation")
                val expectedJson = case.getJSONArray("action")
                val observation = FloatArray(observationJson.length()) { observationJson.getDouble(it).toFloat() }
                val expected = FloatArray(expectedJson.length()) { expectedJson.getDouble(it).toFloat() }
                val actual = policy.action(observation)
                val failed = actual.indices.firstOrNull { index ->
                    abs(actual[index] - expected[index]) >
                        absoluteTolerance + relativeTolerance * abs(expected[index])
                }
                assertTrue("case $caseIndex failed at output $failed", failed == null)
            }
        }
    }
}
