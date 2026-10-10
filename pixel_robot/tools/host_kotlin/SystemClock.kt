package android.os

/** Compile-only Android boundary; never substitutes clock behavior in tests. */
object SystemClock {
    @Suppress("UNUSED_PARAMETER")
    fun sleep(milliseconds: Long): Unit =
        throw UnsupportedOperationException("Android SystemClock is unavailable in host tests")
    fun elapsedRealtime(): Long =
        throw UnsupportedOperationException("Android SystemClock is unavailable in host tests")
    fun elapsedRealtimeNanos(): Long =
        throw UnsupportedOperationException("Android SystemClock is unavailable in host tests")
}
