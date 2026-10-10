package com.leo.pixelrobot.host

import org.json.JSONArray
import org.json.JSONObject
import org.junit.runner.JUnitCore
import org.junit.runner.Description
import org.junit.runner.notification.Failure
import org.junit.runner.notification.RunListener
import java.util.concurrent.atomic.AtomicInteger
import java.util.Collections

internal fun runHostTests(classes: Array<Class<*>>): JSONObject {
    val assumptions = AtomicInteger()
    val started = Collections.synchronizedList(mutableListOf<String>())
    val finished = Collections.synchronizedList(mutableListOf<String>())
    val runner = JUnitCore().apply {
        addListener(object : RunListener() {
            override fun testStarted(description: Description) {
                started.add(description.className + "." + description.methodName)
            }
            override fun testFinished(description: Description) {
                finished.add(description.className + "." + description.methodName)
            }
            override fun testAssumptionFailure(failure: Failure) {
                assumptions.incrementAndGet()
            }
        })
    }
    val result = runner.run(*classes)
    val passed = result.wasSuccessful() && result.ignoreCount == 0 &&
        assumptions.get() == 0 && result.runCount > 0
    return JSONObject()
        .put("status", if (passed) "passed" else "failed")
        .put("test_count", result.runCount)
        .put("test_ids", JSONArray(started.sorted()))
        .put("finished_test_ids", JSONArray(finished.sorted()))
        .put("ignored", result.ignoreCount)
        .put("assumption_failures", assumptions.get())
        .put("elapsed_ms", result.runTime)
        .put("failures", JSONArray(result.failures.map { JSONObject()
            .put("test", it.testHeader).put("message", it.message).put("trace", it.trace) }))
}

fun main(arguments: Array<String>) {
    val report = runHostTests(arguments.map(Class<*>::forName).toTypedArray())
    println(report.toString())
    if (report.getString("status") != "passed") kotlin.system.exitProcess(1)
}
