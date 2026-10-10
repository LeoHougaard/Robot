# Hardware-free Kotlin verification

The host runner compiles the real production policy math, command-operation
arbiter, feedback gates, protocol, recorder and capture code, then runs the
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
Runtime 1.26.0 API, but are not executed by the host unit suite. The production
`RobotControlServer` is exercised on ephemeral loopback sockets with injected
page bytes and fake callbacks; its Android asset-loading branch is untested.
Provider HTTP behavior uses an in-memory fake OkHttp boundary, never an external
endpoint. The minimal host `AssetManager` and `SystemClock` definitions deliberately
throw if invoked, rather than impersonating Android behavior. Android service,
UI and USB implementation files are excluded. No Android ONNX native library
is loaded by this runner.

The separate canonical `pixel-web-ui` check requires Node 24.19.0 and runs the
actual shadow-panel script from `robot_control.html` with authored DOM/fetch
stubs. `node pixel_robot/tools/test_shadow_ui.cjs --output /tmp/shadow-ui.json`
retains source hashes, exact test IDs and the zero-skip result. It needs no npm
packages or browser. It does not execute the existing live-control script or
establish Android WebView rendering/lifecycle behavior.

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

## Owned USB writer and remaining device evidence

`UsbRobotTransport` now uses one application-owned `BoundedSerialWriter`, with
a 16 KiB queue, copied records, cancellation generations, a 40 ms normal write
deadline and a 150 ms safety barrier. Stop discards pending older commands and
waits for the current driver write to end before attempting stop/disarm/torque-off.
A stalled driver closes the port and reports receipt unknown. Teardown runs in
the background and preserves the port for that bounded attempt. Reconnect and
late open/read callbacks are fenced by the service's transport generation.

`BoundedSerialWriterTest` exercises queued and in-flight cancellation, stalled
writes, late completion, disconnect, resumed operations, queue bounds and copied
records, plus close after a completed safety barrier. `PolicyOperationTest` and
`PhysicalCommandAuthorityTest` exercise final-write cancellation, independent
operator approval/readiness, asset identity, monotonic expiry and feedback age.
The operator lease lasts 1..1000 ms, has named body velocity fields and neutral
posture, and rejects longer requests without replacing or extending an existing
lease. Final writes fail at the exact deadline. It is
never constructed from a shadow grant or model callback. There is no UI or HTTP
route that starts this operator-only trial.

Driver completion is not firmware receipt or physical torque-off. The firmware
watchdog may stop policy frames while retaining torque. Pixel/ESP32 acceptance
still requires motor-disabled communication, acknowledgement/readback evidence,
50 Hz timing measurements, app destruction, unplug/replug and link-fault tests.
Physical trials require Leo's separate approval. The phone was disconnected
from the robot during these checks; see [INTEGRATION-STATUS.md](INTEGRATION-STATUS.md).
