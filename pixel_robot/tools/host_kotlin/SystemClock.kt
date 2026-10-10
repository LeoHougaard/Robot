package android.os

/** Compile-only Android boundary; never substitutes clock behavior in tests. */
object SystemClock {
    fun elapsedRealtimeNanos(): Long =
        throw UnsupportedOperationException("Android SystemClock is unavailable in host tests")
}
