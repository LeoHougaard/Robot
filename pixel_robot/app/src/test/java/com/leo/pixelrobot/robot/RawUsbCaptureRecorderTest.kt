package com.leo.pixelrobot.robot

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.BufferedWriter
import java.nio.file.Files
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

class RawUsbCaptureRecorderTest {
    private fun teardown() = JSONObject().put("quiesced", true).put("host_write_outcome", "HOST_WRITE_COMPLETED")
    @Test fun exactFragmentedBytesTimestampsAndSyntheticOriginRoundTrip() {
        val directory = Files.createTempDirectory("raw-usb-test").toFile()
        try {
            val file = directory.resolve("capture.jsonl")
            val clock = AtomicLong(100)
            val recorder = RawUsbCaptureRecorder(file, true, JSONObject(), clock = { clock.getAndIncrement() })
            val fragment = byteArrayOf(0x18)
            recorder.record("tx", fragment, 100)
            fragment[0] = 0
            recorder.record("tx", "\n{\"cmd\":\"hello\"}\n".toByteArray(), 100)
            recorder.record("rx", byteArrayOf(0xff.toByte(), 10), 100)
            val status = recorder.finish("passed", teardown())
            assertTrue(status.complete)
            val records = file.readLines().map(::JSONObject)
            assertEquals(5, records.size)
            assertEquals("18", records[1].getJSONObject("data").getString("hex"))
            assertEquals("ff0a", records[3].getJSONObject("data").getString("hex"))
            assertEquals(100L, records[1].getJSONObject("data").getLong("received_monotonic_ns"))
            assertTrue(records[0].getJSONObject("data").getJSONObject("context").getBoolean("synthetic"))
            records.forEachIndexed { index, record -> assertEquals(index.toLong(), record.getLong("record_index")) }
            assertTrue(records.last().getJSONObject("data").getJSONObject("transport_teardown").getBoolean("quiesced"))
        } finally { directory.deleteRecursively() }
    }
    @Test fun missingOrFailedTeardownIsPreservedWithoutInventingSuccess() {
        val directory = Files.createTempDirectory("raw-usb-test").toFile()
        try {
            val file = directory.resolve("capture.jsonl")
            val recorder = RawUsbCaptureRecorder(file, true, JSONObject())
            recorder.finish("failed", JSONObject().put("quiesced", false).put("host_write_outcome", "WRITE_TIMED_OUT"))
            val end = JSONObject(file.readLines().last()).getJSONObject("data")
            assertEquals("failed", end.getString("outcome"))
            assertFalse(end.getJSONObject("transport_teardown").getBoolean("quiesced"))
        } finally { directory.deleteRecursively() }
    }
    @Test fun overflowNeverBlocksProducerAndMakesCaptureIncomplete() {
        val directory = Files.createTempDirectory("raw-usb-test").toFile()
        val entered = CountDownLatch(1); val release = CountDownLatch(1)
        try {
            val file = directory.resolve("capture.jsonl")
            val recorder = RawUsbCaptureRecorder(file, true, JSONObject(), queueCapacity = 1, openWriter = { target ->
                object : BufferedWriter(target.writer()) {
                    override fun write(text: String, offset: Int, length: Int) {
                        if (text.contains("usb_rx_chunk")) { entered.countDown(); check(release.await(5, TimeUnit.SECONDS)) }
                        super.write(text, offset, length)
                    }
                }
            })
            recorder.record("rx", "one\n".toByteArray(), 0)
            assertTrue(entered.await(1, TimeUnit.SECONDS))
            val producer = java.util.concurrent.Executors.newSingleThreadExecutor()
            try { producer.submit { repeat(50) { recorder.record("rx", "two\n".toByteArray(), 0) } }.get(1, TimeUnit.SECONDS) }
            finally { producer.shutdownNow() }
            release.countDown()
            val status = recorder.finish("failed", teardown())
            assertFalse(status.complete); assertTrue(status.droppedRecords > 0)
            val end = JSONObject(status.file.readLines().last()).getJSONObject("data")
            assertFalse(end.getBoolean("complete")); assertTrue(end.getLong("dropped_records") > 0)
        } finally { release.countDown(); directory.deleteRecursively() }
    }
    @Test fun oversizedChunksAndFutureCallbackInvalidate() {
        val directory = Files.createTempDirectory("raw-usb-test").toFile()
        try {
            val recorder = RawUsbCaptureRecorder(directory.resolve("capture.jsonl"), true, JSONObject(), clock = { 10 }, maxChunkBytes = 4)
            recorder.record("rx", ByteArray(5), 0)
            recorder.record("rx", byteArrayOf(1), 11)
            val status = recorder.finish("failed", teardown())
            assertFalse(status.complete); assertEquals(2L, status.droppedRecords)
        } finally { directory.deleteRecursively() }
    }
    @Test fun sizeCapProducesIncompleteEndRecord() {
        val directory = Files.createTempDirectory("raw-usb-test").toFile()
        try {
            val file = directory.resolve("capture.jsonl")
            val recorder = RawUsbCaptureRecorder(file, true, JSONObject(), maxCaptureBytes = 8192)
            recorder.record("rx", ByteArray(4000) { 65 }, 0)
            val status = recorder.finish("failed", teardown())
            assertFalse(status.complete)
            assertTrue(status.file.length() <= 8192)
            assertEquals("session_end", JSONObject(status.file.readLines().last()).getString("type"))
        } finally { directory.deleteRecursively() }
    }
    @Test fun finalizationTimeoutNeverPassesAndLateFinishRemainsIncomplete() {
        val directory = Files.createTempDirectory("raw-usb-test").toFile()
        val entered = CountDownLatch(1); val release = CountDownLatch(1)
        try {
            val file = directory.resolve("capture.jsonl")
            val recorder = RawUsbCaptureRecorder(file, true, JSONObject(), queueCapacity = 1, openWriter = { target ->
                object : BufferedWriter(target.writer()) {
                    override fun write(text: String, offset: Int, length: Int) {
                        if (text.contains("usb_rx_chunk")) { entered.countDown(); check(release.await(5, TimeUnit.SECONDS)) }
                        super.write(text, offset, length)
                    }
                }
            })
            recorder.record("rx", byteArrayOf(1), 0)
            assertTrue(entered.await(1, TimeUnit.SECONDS))
            recorder.record("rx", byteArrayOf(2), 0)
            assertFalse(recorder.finish("passed", teardown(), timeoutMs = 20).complete)
            release.countDown()
            val status = recorder.finish("passed", teardown())
            assertFalse(status.complete)
            assertFalse(JSONObject(status.file.readLines().last()).getJSONObject("data").getBoolean("complete"))
        } finally { release.countDown(); directory.deleteRecursively() }
    }
    @Test fun storageFailureInvalidatesEvidence() {
        val directory = Files.createTempDirectory("raw-usb-test").toFile()
        try {
            val file = directory.resolve("capture.jsonl")
            val recorder = RawUsbCaptureRecorder(file, true, JSONObject(), openWriter = { target ->
                object : BufferedWriter(target.writer()) {
                    override fun write(text: String, offset: Int, length: Int) {
                        if (text.contains("usb_rx_chunk")) throw java.io.IOException("authored failure")
                        super.write(text, offset, length)
                    }
                }
            })
            recorder.record("rx", byteArrayOf(1), 0)
            val status = recorder.finish("failed", teardown())
            assertFalse(status.complete)
            assertFalse(JSONObject(status.file.readLines().last()).getJSONObject("data").getBoolean("complete"))
        } finally { directory.deleteRecursively() }
    }
    @Test fun refusesOverwriteAndCallbackAfterFinishCannotSilentlySucceed() {
        val directory = Files.createTempDirectory("raw-usb-test").toFile()
        try {
            val file = directory.resolve("capture.jsonl")
            val recorder = RawUsbCaptureRecorder(file, true, JSONObject())
            recorder.finish("passed", teardown())
            try { RawUsbCaptureRecorder(file, true, JSONObject()); fail("must refuse overwrite") } catch (_: IllegalArgumentException) {}
            recorder.record("rx", byteArrayOf(1), 0)
            // A finalized capture is immutable; a late callback is an explicit error
            // in the status, not a second session or an appended clean record.
            assertFalse(recorder.finish("passed", teardown()).complete)
        } finally { directory.deleteRecursively() }
    }
}
