package androidx.test.core.app

/** Signature-only instrumentation boundary. Never runs an Android test on the host. */
object ApplicationProvider {
    fun <T> getApplicationContext(): T =
        throw UnsupportedOperationException("Android instrumentation is unavailable in host compilation")
}
