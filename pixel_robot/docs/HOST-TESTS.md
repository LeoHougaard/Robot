# Hardware-free Kotlin verification

The host runner compiles the real production policy math, command-operation
arbiter, feedback gates, bounded USB writer, physical-intent authority, protocol,
recorder and capture code, then runs the
repository's JUnit tests. It requires Java 17 or newer and Python 3.10 or newer.
It does not need Gradle, javac, an Android SDK, an emulator, a phone or a robot.

Setup once on an authorized network, from the repository root:

```sh
python pixel_robot/tools/run_host_kotlin_tests.py --download-only
```

This explicitly downloads pinned official Maven Central artifacts with SHA-256
verification into ignored `pixel_robot/.tooling/host-kotlin/`. The dependency
lock is `tools/host_kotlin/dependencies.json`; no installation, Android SDK
license acceptance, or device connection occurs. `curl` is required only for
setup. Preserve this cache for subsequent offline runs, or use `--cache-dir`.

Run a clean source compile and tests offline:

```sh
python pixel_robot/tools/run_host_kotlin_tests.py --rebuild --output /tmp/pixel-host-tests.json
```

A missing dependency or Java runtime is **blocked** (exit 2), not a pass or
skip. Compile/test failure exits 1. Successful tests require a positive test
count, no failures, no ignored tests and zero `assumption_failures`. A JUnit
assumption violation is failed evidence even when JUnit reports no ordinary
failure. A dedicated runner regression and an isolated fixture cover this
distinction. The JSON report lists requested test classes and the actual started
and finished test IDs, plus precise compiled source scope, excluded files and
Java runtime. The canonical validator requires matching, complete execution of
the shadow-session, loaded-assets, coordinator, provider and route suites.
A clean rebuild uses a
new class directory, so it cannot disrupt another running compute-only bridge.
After success it atomically publishes a source-fingerprint pointer to that
immutable, byte-hash-verified build. Concurrent compilers never write to the
same classes directory, and subsequent bridge preparation reuses the published
build rather than compiling again inside a request timeout.

## What this does not establish

`PolicyController` and `OnnxPolicy` are compiled against the exact Android ONNX
Runtime 1.26.0 API, but are not executed by the host unit suite. `RobotService`
and `UsbRobotTransport` are now also compiled to catch internal Kotlin wiring
errors, against explicit throwing Android/AndroidX/USB signature shims under
`tools/host_kotlin/android_compile`. Those shims are not real framework or USB
driver implementations and cannot verify library compatibility. `MainActivity`
and generated resources are type placeholders; the real Activity, CameraX,
view binding, resources and packaging remain uncompiled. Reports retain explicit
false Android API/lifecycle, USB driver, MainActivity and app-build verification
flags. No Android SDK or license acceptance is part of this host check.

`Installed3750MonitorTransportTest.kt` is separately listed as compile-only. Its
instrumentation signatures are throwing shims under `android_test_compile`, and
the runner never adds this class to the JUnit execution list. This catches typed
raw-capture/transport wiring only. `android_instrumentation_verified` stays false;
no installed application, device harness or USB capture is executed here.

The production
`RobotControlServer` is exercised on ephemeral loopback sockets with injected
page bytes and fake callbacks; its Android asset-loading branch is untested.
Provider HTTP behavior uses an in-memory fake OkHttp boundary, never an external
endpoint. The minimal host `AssetManager` and `SystemClock` definitions deliberately
throw if invoked, rather than impersonating Android behavior. No Android ONNX native library
is loaded by this runner.

The separate canonical `pixel-web-ui` and `pixel-physical-ui` checks require
Node 24.19.0 and run the actual separate panel scripts from `robot_control.html`
with authored DOM/fetch stubs.
`node pixel_robot/tools/test_shadow_ui.cjs --output /tmp/shadow-ui.json`
retains source hashes, exact test IDs and the zero-skip result. It needs no npm
packages or browser. It does not execute the existing live-control script or
establish Android WebView rendering/lifecycle behavior. The corresponding
physical-panel command is `node pixel_robot/tools/test_physical_ui.cjs --output
/tmp/physical-ui.json`; its result explicitly reports no physical execution.
That suite additionally extracts the narrow manual HTTP helper, status/button
logic and page exit/restoration callbacks to verify operator-token headers,
local denial when a token is absent, generation-bound pending Start/Stand,
and an authenticated keepalive Stop. It does not run the full manual controller
script, a browser, or any live bridge endpoint.

This is not an Android app build, Android lifecycle test, Pixel timing test,
USB test, firmware test, hardware safety qualification, or walking evaluation.
Run `gradlew test assembleDebug` with the repository's required Android SDK and
Gradle toolchain separately. Instrumentation/installation/robot tests require
separate operator authorization. No Android/device pass is inferred here.

Historical V2/V20 tests use explicit historical/synthetic contracts and the
legacy calibration fixture. A dedicated provenance test verifies the exact
Windows CRLF byte hash embedded in the V20 sensor fixture against the Git LF
calibration, without changing the golden observations or tolerances. The
shipped V22 metadata, ONNX weights, stride
reference and calibration remain unchanged. Their live-activation guard is
tested separately and does not confer promotion or physical acceptance.

## Compute-only production-policy bridge

For integration callers, prepare the bridge separately before issuing a lease,
grant or timed request:

```sh
python pixel_robot/tools/run_host_kotlin_tests.py --bridge-command
```

This does not start a peer. On success it returns `status: bridge_ready`, the
verified source fingerprint, and `command`, an argument array for launching the
JVM directly without a shell. Compilation has its own 180-second deadline;
allow 240 seconds for this preparation process. A launched JVM then uses the
caller's separate short per-operation timeout. No compilation happens in that
RPC window. Cold builds and clean `--rebuild` results use the same publication
contract. Missing dependencies still block without network access.

For an interactive, untimed session, the convenience launcher can prepare and
start the peer itself:

```sh
python pixel_robot/tools/run_host_kotlin_tests.py --bridge
```

The process accepts one JSON object per stdin line and flushes one JSON object
per response. The launcher becomes the JVM process, so cancellation does not
leave an orphan Java reader. It loads only the bundled V22 epoch-3750 metadata,
stride reference and calibration, checks the production activation/calibration
contract and reference hash, and performs no USB, network or actuator writes.
All completion acknowledgements in this tool are explicitly synthetic.

Operations:

- `{"op":"reset"}` resets frame/history/stride state and returns asset hashes,
  checkpoint, coordinate convention and observation width
- `observe`: fields `command` (forward, lateral, yaw), `posture` (height, roll,
  pitch), `acknowledged_action` (12), and `sensor` containing `imu` (6),
  `position` (12), `velocity` (12), `current` (12 raw integers or null), and
  `dt` (seconds). Returns the exact production 428-value observation
- `action`: fields `residual` (12), `command`, `posture`, and `gravity` (3).
  Command, posture and gravity must match the immediately observed frame.
  Returns production filtered/applied actions, diagnostic `targets_deg` and
  `servo_ids` in policy order
- `{"op":"complete"}` advances the clock after one observation and action,
  acknowledging only a synthetic host frame
- `{"op":"close"}` exits cleanly

Invalid bounds, non-finite/wrong-sized fields, illegal phase transitions,
asset mismatch, or calibrated target violations return a sanitized failure
and terminate nonzero. This is an evidence adapter for synthetic/replay
inference, never a controller endpoint and never motion authorization.

## Bounded USB ordering, startup and teardown

`UsbRobotTransport` now routes every native write through the production
`BoundedUsbWriter`; the third-party `SerialInputOutputManager` is read-only and
its private `writeAsync` buffer is never used. Host tests execute the real pure
writer and startup fence. The Android adapter/service compile against throwing
API signatures; neither the actual driver nor Android lifecycle is executed.

- Ordinary pending storage is bounded to 64 records / 16 KiB, with a 6,144-byte
  maximum record. Overflow raises explicit backpressure. Records are copied on
  admission. A separate reserved slot contains the fixed stop/disarm/torque-off
  batch, so ordinary saturation cannot prevent its admission
- Opaque generations belong to one writer instance. Stop, close, failure and a
  new operation revoke queued old work. No operation can rebind old queued bytes
  to a replacement USB connection. After Stop, ordinary writes remain rejected
  until an explicit new operation advances generation
- The deferred controller/physical-authority check encloses only the final
  in-flight claim, with no writer lock held while acquiring authority locks.
  Blocking native I/O executes after those locks are released. Stop cannot
  recall a claimed write; it waits for that write to finish successfully before
  attempting safety. A rejected/expired final claim instead requests safety
- Each native write has a 100 ms driver timeout plus an independent watchdog.
  Stop has a 500 ms whole-attempt deadline. Failure/timeout may leave partial
  JSON, so the writer closes instead of appending a safety batch to that unknown
  prefix. Main-thread stop/close calls only schedule background work
- `UsbTeardown.outcome` is a bounded host result; even `HOST_WRITE_COMPLETED`
  always has `firmwareAcknowledged=false`. `quiesced` succeeds only after the
  actual old port close and in-flight native write have both returned. A stuck
  write/close leaves reconnect blocked, and a close error completes it
  exceptionally. The service never substitutes a deadline result for quiescence

Unknown firmware is fenced before the first input delimiter: startup sends
ASCII CAN (0x18) plus a fresh 32-character lowercase-hex nonce, with **no LF**.
The paired firmware clears its partial input at CAN and acknowledges the exact
nonce as `serial_abort`, version 2. Only that exact current response permits the
first LF and ordinary hello/commands. Pre-request, stale, malformed or truncated
acknowledgements do not grant readiness. After 750 ms without a matching ACK,
startup closes without a newline-bearing safety batch. This prevents reconnect
from accidentally completing an older buffered arm/torque JSON command. Older
firmware is deliberately rejected; migration requires separately authorized,
motor-power-off firmware deployment, not a fallback to the old parser.

The host suites cover queue limits, selected/queued/in-flight races, timeout
versus completion, generation-safe reconnect, reentrant/cross-thread stop,
blocked native write/close, rejected executors/schedulers, faulty deadline
cancellation, stale authority callbacks and nonce/framing faults. Their clocks,
executors and sinks are fake; latch-based tests control actual test-thread
interleavings without claiming device scheduling or serial delivery timing.

Still required under separate authorization: actual Android build/lifecycle,
paired Pixel/ESP32 startup and unplug/replug traces, 50 Hz timing under load,
firmware acknowledgements and torque-register readback. The optional raw-wire
observer records RX before filtering and exact native TX **attempts** (including
startup and safety framing); TX capture does not establish transmission or
receipt. The firmware watchdog may retain servo torque while stopping stale
policy frames. No host test or teardown future proves physical torque-off.

## Local reconciliation status

The prior local Android and device integration evidence remains in INTEGRATION-STATUS.md and the checkpoint handoff. It predates this V5 reconciliation and does not validate the new transport protocol. The one-second operator-only PhysicalCommandAuthority remains available alongside the separate one-shot PhysicalIntentAuthority. Both require independent authority; the bundle remains unpromoted and physical transport unverified. Current reconciliation results are recorded in V5-RECONCILIATION.md.
