package com.leo.pixelrobot.host

import org.junit.Assert.*
import org.junit.Assume
import org.junit.Test

class HostTestRunnerContractTest {
    @Test
    fun assumptionViolationIsFailedEvidenceEvenWithoutAJUnitFailureOrIgnore() {
        val report = runHostTests(arrayOf(AssumptionFixture::class.java))
        assertEquals("failed", report.getString("status"))
        assertEquals(1, report.getInt("test_count"))
        assertEquals(0, report.getInt("ignored"))
        assertEquals(1, report.getInt("assumption_failures"))
        assertEquals(0, report.getJSONArray("failures").length())
        val ids = report.getJSONArray("test_ids")
        assertEquals(1, ids.length())
        assertEquals("com.leo.pixelrobot.host.AssumptionFixture.prerequisiteUnavailable", ids.getString(0))
        assertEquals(ids.toString(), report.getJSONArray("finished_test_ids").toString())
    }
}

class AssumptionFixture {
    @Test
    fun prerequisiteUnavailable() {
        Assume.assumeTrue("intentional runner-regression fixture", false)
    }
}
