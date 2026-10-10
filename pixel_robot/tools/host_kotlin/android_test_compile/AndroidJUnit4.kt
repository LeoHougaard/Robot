@file:Suppress("UNUSED_PARAMETER")
package androidx.test.ext.junit.runners

import org.junit.runner.Description
import org.junit.runner.Runner
import org.junit.runner.notification.RunNotifier

/** Compile-only annotation target; executing an instrumentation class must fail closed. */
class AndroidJUnit4(testClass: Class<*>) : Runner() {
    override fun getDescription(): Description =
        throw UnsupportedOperationException("Android instrumentation is unavailable on the host")
    override fun run(notifier: RunNotifier): Unit =
        throw UnsupportedOperationException("Android instrumentation must never run on the host")
}
