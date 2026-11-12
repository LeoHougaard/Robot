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

    @Test
    fun cadV2UsesItsExplicitLiftFadeSpeeds() {
        val spec = JSONObject(bytes.toString(Charsets.UTF_8))
            .put("kind", "stride_reference_cad_v2")
            .put("joint_coordinate_convention", "cad_drives_v1")
            .put("lift_m", .03)
            .put("lift_full_planar_speed_m_s", .02)
            .put("lift_full_yaw_rate_rad_s", .10)
        val v2Bytes = spec.toString().toByteArray(Charsets.UTF_8)
        val reference = StrideReference.parse(v2Bytes, sha256(v2Bytes), profile)
        val stop = reference.combined(FloatArray(12), floatArrayOf(0f, 0f, 0f),
            FloatArray(3), floatArrayOf(0f, 0f, -1f), 3f)
        assertTrue(stop.all { it == 0f })
        val identity = JSONArray("[[1,0,0],[0,1,0],[0,0,1]]")
        spec.put("nominal_feet_m", JSONArray("[[0,0,0],[0,0,0],[0,0,0],[0,0,0]]"))
            .put("inverse_jacobians", JSONArray().also { a -> repeat(4) { a.put(identity) } })
            .put("phase_offsets", JSONArray("[0,0,0,0]"))
            .put("frequency_hz", 1.0).put("settle_seconds", 0.0).put("ramp_seconds", 1.0)
        val probeBytes = spec.toString().toByteArray(Charsets.UTF_8)
        val probe = StrideReference.parse(probeBytes, sha256(probeBytes), profile)
        for (command in arrayOf(floatArrayOf(0f, .02f, 0f), floatArrayOf(0f, 0f, .10f))) {
            val targets = probe.combined(FloatArray(12), command, FloatArray(3),
                floatArrayOf(0f, 0f, -1f), 2.8f)
            for (foot in 0..3) assertTrue(kotlin.math.abs(targets[3 * foot + 2] - .1f) < 1e-5f)
        }
        val tiny = probe.combined(FloatArray(12), floatArrayOf(0f, .000001f, 0f),
            FloatArray(3), floatArrayOf(0f, 0f, -1f), 2.8f)
        for (foot in 0..3) assertTrue(tiny[3 * foot + 2] in 0f..1e-5f)
    }
}

private fun JSONArray.floats() = FloatArray(length()) { getDouble(it).toFloat() }
private fun sha256(bytes: ByteArray): String = java.security.MessageDigest.getInstance("SHA-256")
    .digest(bytes).joinToString("") { "%02x".format(it) }
