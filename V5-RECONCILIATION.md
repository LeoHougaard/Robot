# V5 reconciliation on LEO-PC

Branch `review/robot-v5-reconciled` starts at
`6daea82f515e8e5352321172ec96b486ba6d5bb9`. The reference is Library version 4,
`robot-llm-hardening-v5`, ZIP SHA256
`cd9c0760fadbb9cceaa87f0f823d6d252664dba33da42e2871d2262b34d7fc60`.
Changes were compared against the exact earlier V4 tree
`a314e14f06b38d66daa3c5055cbc0c18e6296529`; no forced patch or checkout reset was used.

## Scope

- Merged: all HTTP mutation token/origin checks; generation-bound manual Start
  and Stand; one-request physical intent consent bound to an existing manual
  operation; bounded writer; nonce-v2 startup; firmware boundary/watchdog guards;
  current-or-previous command acknowledgement guards; raw capture and acceptance
  tools, fake-I/O regression suites and Android diagnostic source.
- Already present and preserved: immutable policy assets, strict JSON decoding,
  provider cancellation and absolute eight-second publication deadline, explicit
  literal-loopback Bonsai support, local Android SDK/build fixes, V22 epoch3750
  with 428 inputs and `cad_drives_v1`, and 2,000,000 baud. Policy assets and
  promotion/physical-transport claims are unchanged.
- Adapted: retained the direct native USB reader supported by driver 3.11.0.
  Reconnect quiescence now includes reader termination as well as old native
  write and port close. Callback identity belongs to the exact transport object.
  The retained one-second operator-only authority remains separate from the new
  one-shot text authority. Its queued checks capture the original lease and
  feedback timestamp; a new manual operation revokes the previous operator lease.
  Operation, bundle, deadline and generation checks remain at final dispatch.
- Adapted test: the legacy writer close-count assertion waits for its close
  callback latch. Driver and stop deadlines and all outcome assertions remain.
- Deferred: installed-device lifecycle/WebView checks, real provider qualification,
  firmware flashing, torque-disabled serial acceptance and all physical trials.
  Wheeled navigation remains separate and paused.

## Verification and limits

Evidence resides in `C:\Users\Leo\Documents\Codex\2026-10-10\task`, outside Git.
The first source-bound full canonical run, `v5-native-validation/latest.json`,
passed all 21 functional gates plus source/dependency stability, including 285
Kotlin host tests and independent shadow/physical UI gates. Final source checks
passed in `v5-final-validation-pass` (1,151 tests plus seven CPU subtest reports, zero failures or skips) and `v5-android-release-check.log` (279 unit tests; debug and instrumentation APK builds).
The earlier `v5-final-validation` BLOCKED attempt omitted an existing pinned AAR from a temporary cache copy; the complete manifest-pinned cache fixed this without changing dependency versions or tests. This final evidence paragraph was added after testing; production and test source bytes are unchanged.
Windows linked-worktree metadata is unreadable to Linux Git; validation uses a
normal disposable clone and a byte-hash-verified native-filesystem mirror.
`v5-source-proof.json` binds the exact copied files.

Android unit tests and debug plus instrumentation APK compilation use the
existing SDK/JDK/Gradle with offline mode and downloads disabled. Instrumentation
compilation is not device execution. Firmware host validation passed, including
14 adversarial fake-I/O replay scenarios. ESP32 binary generation passed with
installed espressif32 7.0.1, existing framework 3.20017.241212+sha.dcc1105b and
cached ArduinoJson 7.4.3. The source-pinned cloud compile-only environment remains
separate; its historical evidence was preserved. Local binary hashes and exact
source hashes are in `robot_dog/firmware/robot-dog-control/test/local-v5-compile-evidence.json`.
The compile log retains the blocked PlatformIO post-build update request after
the SUCCESS/binary-generation result; no network download was permitted.

No APK installation, SDK terms acceptance, provider call, backend job, training,
simulation workload, device/serial access, flashing, physical motion or push
occurred. No software result certifies physical safety.

## Next physical acceptance boundary

Leo must first confirm the twelve-servo dog and exact calibration/bundle identity.
Keep `promoted:false` and `physical_transport_verified:false`. Under separately
approved motor-power-off installation, deploy the paired nonce-v2 firmware and
app, then follow `pixel_robot/docs/FIRST-ROBOT-TEST.md` and
`deployment_readiness/HARDWARE-ACCEPTANCE.md`: preserve raw startup, acknowledgement,
torque-register readback, 50 Hz scheduling, power-cycle, unplug/replug, app-stop
and link-fault evidence. A native TX attempt or host close result proves neither
firmware receipt nor physical torque-off. Physical command trials require
explicit operator approval and independent stopping arrangements.
