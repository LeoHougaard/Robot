package com.leo.pixelrobot.robot

/** Ephemeral HTTP admission epoch. Never a physical/USB permission by itself.
 * Hold this monitor only across short start/stand admission, never lifecycle I/O.
 * Stop invalidates first, releases this monitor, then performs its independent stop.
 */
class ManualOperationGate {
    private var epoch = 0L
    val generation: Long @Synchronized get() = epoch
    @Synchronized fun invalidate() { epoch = Math.addExact(epoch, 1) }
    @Synchronized fun admit(expectedGeneration: Long, stillTimely: () -> Boolean = { true }, action: () -> Unit) {
        check(expectedGeneration == epoch && stillTimely()) { "stale_or_expired_manual_operation" }
        epoch = Math.addExact(epoch, 1) // A failed/replayed start still consumes this admission.
        action()
    }
}
