@file:Suppress("UNUSED_PARAMETER")
package androidx.test.platform.app

/** Only the used signatures; this is not Android Bundle/Instrumentation emulation. */
class CompileOnlyArguments {
    fun getString(key: String): String? =
        throw UnsupportedOperationException("Android instrumentation is unavailable in host compilation")
}
object InstrumentationRegistry {
    fun getArguments(): CompileOnlyArguments =
        throw UnsupportedOperationException("Android instrumentation is unavailable in host compilation")
}
