package com.leo.pixelrobot.policy

import java.io.File
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class StrideReferenceTest {
    private val fixture = JSONObject(File("src/test/resources/stride_reference_v1_parity.json").readText())
    private val bytes = fixture.getString("reference_utf8").toByteArray(Charsets.UTF_8)
    private val hash = fixture.getString("reference_sha256")
    private val profile = fixture.getString("profile_sha256")

    @Test
    fun matchesActualTrainingReferenceAcrossStartupStridesAndOneMinute() {
        val reference = StrideReference.parse(bytes, hash, profile)
        val cases = fixture.getJSONArray("cases")
        assertTrue(cases.length() >= 150)
        for (i in 0 until cases.length()) {
            val row = cases.getJSONObject(i)
            val elapsed = row.getDouble("elapsed_seconds").toFloat()
            val combined = reference.combined(
                row.getJSONArray("residual").floats(), row.getJSONArray("command").floats(),
                row.getJSONArray("posture").floats(), row.getJSONArray("gravity").floats(), elapsed,
            )
            assertArrayEquals("combined targets case $i", row.getJSONArray("combined").floats(), combined, 3e-5f)
            assertArrayEquals("actor clock case $i", row.getJSONArray("clock").floats(), reference.clock(elapsed), 3e-5f)
        }
    }

    @Test
    fun rejectsMismatchedReferenceAndRobotProfile() {
        assertTrue(runCatching { StrideReference.parse(bytes, "0".repeat(64), profile) }.isFailure)
        assertTrue(runCatching { StrideReference.parse(bytes, hash, "0".repeat(64)) }.isFailure)
    }

    @Test
    fun rejectsInvalidClockOrFeedbackBeforeProducingTargets() {
        val reference = StrideReference.parse(bytes, hash, profile)
        assertTrue(runCatching { reference.clock(Float.NaN) }.isFailure)
        assertTrue(runCatching { reference.clock(-.02f) }.isFailure)
        assertTrue(runCatching { reference.clock(Float.MAX_VALUE) }.isFailure)
        assertTrue(runCatching {
            reference.combined(FloatArray(12), FloatArray(3), FloatArray(3),
                floatArrayOf(Float.NaN, 0f, -1f), 2f)
        }.isFailure)
    }
}

private fun JSONArray.floats() = FloatArray(length()) { getDouble(it).toFloat() }
