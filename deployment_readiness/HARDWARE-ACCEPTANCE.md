# First-hardware capture acceptance (offline analyzer)

This probe reads an explicitly supplied local JSONL capture. It never enumerates
USB, opens a serial device, runs ADB, invokes a backend, flashes firmware, sends
commands, imports robot runtime/ONNX, or enables torque. No SDK or third-party
Python dependencies are needed. Its input must be a regular file (not a device,
FIFO, symlink, ZIP, or stdin), at most 128 MiB. Output is stdout only.

The current body must be confirmed first: `dog12` means the calibrated twelve
servos with unique IDs 1 through 12. Rover support is **not** inferred from the
presence of two auxiliary motors. An unspecified body or `--target rover`
returns BLOCKED. Leo owns any hardware operation and all motion.

## Run against an existing real Pixel capture

From the repository root (no hardware access):

```sh
python -B -m deployment_readiness.hardware_acceptance /absolute/path/monitor.jsonl --target dog12 --evidence-kind operator-capture --expected-firmware 0.1.18-transportguard
```

Replace the expected firmware label with the *specific build the operator
intends to test*, then compare it with the captured hello. The analyzer default
is the new source-only 0.1.18-transportguard candidate, not an assertion about
the installed board. The paired new app reconnect preamble requires this
firmware and its `supportsSerialInputAbort=true`, `serialInputAbortVersion=2` capabilities; an old .17 hello
remains BLOCKED even if supplied as the expected label. A version
string cannot establish a firmware binary hash. Any new source-only candidate,
including 0.1.18-transportguard, must not be described as installed without a
separate verified operator installation. Do not flash as an incidental repair.

`--format text` prints the checklist; JSON is the default and includes SHA-256
of the capture. Shell redirection may save stdout; the analyzer has no output
file option. Exit codes: `0 PASS`, `1 FAIL`, `2 BLOCKED`.

- PASS means the supplied capture satisfies the stated checks only
- FAIL means observed data contradicts a check (errors, stale feedback,
  wrong identity, unsafe commands, dropped recording, malformed data)
- BLOCKED means the body, source, completeness, coverage, or a required
  measurement is absent. Missing measurements never pass
- FAIL takes precedence over BLOCKED, but every available check remains visible
- `physical_execution`, `physical_safety_certified`,
  `usb_transport_independently_verified`, and `motion_authorized` remain false

Use existing `pixel_robot/tools/inspect_run_data.py`,
`fit_sim_from_run_data.py`, and `analyze_training_capture.py` for ordinary
run/ZIP validation, graphs and simulation identification. This probe complements
them with first-hardware torque-off criteria; it does not replace their analyses.

## Actual operator-only Pixel collection path

Reuse the checked-in, explicitly gated test:

`pixel_robot/app/src/androidTest/java/com/leo/pixelrobot/policy/Installed3750MonitorTransportTest.kt`

It uses `UsbRobotTransport`, `JsonLineDecoder`, and `RunSessionRecorder` from
`pixel_robot/app/src/main/java/com/leo/pixelrobot/robot/`, plus the new bounded
`RawUsbCaptureRecorder` and the installed
policy's inference/sensor/action pipeline. Its explicit monitoring commands are `hello`, `policy_monitor`,
`policy_monitor_frame`, and `policy_disarm`. The new transport also adds an
initial delimiter-free CAN (0x18) + fresh 32-lowercase-hex nonce challenge.
Only a version-2 `serial_abort` reply echoing that nonce permits the first LF.
It also includes an asynchronous reserved
teardown batch: `stop`, `policy_disarm`, and `servo_torque` with `all=true`,
`enabled=false`. This is torque-disable teardown, never torque-enable or
motor-position writing. Monitor frames are parsed and their
targets discarded by firmware. Admission requires the servo rail and verified
disabled torque; inability to read registers is a refusal, not proof of torque.
Do not bypass admission, substitute an arm command, or use this on another body.
Disarm is not proof that physical torque has been disabled.

Prerequisites belong to the operator: verified intended body/board/firmware,
legs supported, torque already off, servo rail present, Pixel USB host permission,
appropriate reviewed app/test APKs already installed, and an explicitly verified
ADB device serial. This document does not authorize installing/flashing/starting
anything. Do not reuse historical IP addresses or COM names.

When the operator explicitly chooses to run the diagnostic (repository root):

```sh
adb devices -l
# Replace PHONE_SERIAL with the exact intended, currently authorized device
adb -s PHONE_SERIAL shell am force-stop com.leo.pixelrobot
adb -s PHONE_SERIAL shell am instrument -w -e robot_hardware torque_off -e policy_candidate epoch3750 -e class com.leo.pixelrobot.policy.Installed3750MonitorTransportTest com.leo.pixelrobot.test/androidx.test.runner.AndroidJUnitRunner
adb -s PHONE_SERIAL shell run-as com.leo.pixelrobot ls files/transport_diagnostics
adb -s PHONE_SERIAL exec-out run-as com.leo.pixelrobot cat files/motor-disabled-transport-result.json > monitor-result.json
# Read raw_capture_file from monitor-result.json and use that EXACT filename
# below (raw-usb-UUID.jsonl on successful finalization, .partial on failure)
adb -s PHONE_SERIAL exec-out run-as com.leo.pixelrobot cat files/transport_diagnostics/EXACT_RAW_CAPTURE_FILENAME > EXACT_RAW_CAPTURE_FILENAME
python -B -m deployment_readiness.hardware_acceptance EXACT_RAW_CAPTURE_FILENAME --target dog12 --evidence-kind operator-capture --expected-firmware EXPECTED_BUILD_LABEL
```

These redirections assume a byte-preserving POSIX shell. On Windows use a
byte-preserving shell (for example `cmd.exe` redirection); legacy Windows
PowerShell may transcode redirected native output. Retain both the exact raw
JSONL and the instrumentation result. The latter separately records inference,
send/frame timing, callbacks and startup partial lines; this analyzer does not
read or promote that sidecar into a passing gate. `session_end.complete=false`,
dropped records, or firmware errors invalidate the attempt.

The diagnostic writes two complementary files. The sidecar's `raw_capture_file`
identifies the exact acceptance input: `RawUsbCaptureRecorder` records TX at the
native-write-attempt boundary, including the nonce handshake and reserved safety
batch; RX is captured before handshake filtering/JSON decoding. It therefore
retains malformed bytes and chunk boundaries. `raw_capture_complete` must be
true; preserve an unfinished `.partial` extension rather than renaming it to
hide failed finalization. On failure, inspect the partial file for diagnosis,
but do not treat it as acceptance. The CLI blocks partial/interrupted paths.

The helper uses a 1,024-record bounded background queue plus one reserved
finalization slot, a 65,536-byte per-chunk cap, and a 64 MiB capture cap. Producers
never wait for storage. Overflow, size cap, I/O failure or bounded finalization
timeout invalidates the recording. A `.jsonl.partial` is promoted to `.jsonl`
only after successful flush/close. Provenance is explicitly operator capture in
the instrumentation and synthetic in all offline tests.

The existing parallel recorder still creates Pixel schema-1 JSONL with `session_start`,
`robot_tx`, `robot_rx`, `derived_policy_frame`, and `session_end`; no conversion
or invented wrapper is required. The analyzer consumes `data.message` from
`robot_tx`/`robot_rx` and `host_monotonic_ns`. It requires the original session,
sequential `record_index`, stable `session_id`, final `complete=true`, and
`dropped_records=0`. Do not trim or merge sessions for this acceptance check.

**Expected limitation of the current production capture:** parsed `robot_rx`
records omit malformed lines/chunk boundaries, and do not preserve the original
USB callback timestamp. Therefore byte framing and feedback handling age are
BLOCKED, even if every visible telemetry check passes. A recorded `robot_tx`
is queue admission, not confirmed native USB write or firmware receipt. Any
new host-write-completed event must likewise not be confused with a firmware
acknowledgement. A telemetry-only capture is still useful for diagnosing losses,
servo/IMU faults, errors and timing, but cannot clear full USB acceptance.

The diagnostic now observes `closeAsync().outcome` and `closeAsync().quiesced`
with bounded waits before finishing the raw capture. It also requests a closing
hello after the monitor drain so UART counters bracket all sampled states.
Failure to observe teardown invalidates the attempt and blocks reconnect claims.
Even a successful native write is not a verified physical stop or torque-off
acknowledgement.

## Raw-byte evidence contract, version 1

The checked-in `RawUsbCaptureRecorder` produces this format; no custom Codex
capture implementation or log conversion is required. Hardware execution and
Android deployment remain separately unverified.

Use the same schema-1 session/index/time envelope. Replace parsed `robot_rx`
and `robot_tx` with `usb_rx_chunk` and `usb_tx_chunk` respectively; never include
both encodings for the same direction. `data.hex` is a nonempty, even-length
hex byte string without whitespace. It contains exact bytes including newlines
and padding. Fragmentation and coalescing are permitted. Each direction has an
independent streaming decoder. LF terminates a frame, surrounding ASCII
whitespace/CR padding is ignored, UTF-8 is strict, duplicate JSON keys and
nonfinite numbers are rejected. Attaching midstream may expose an initial partial
line. Only invalid lines **before a fresh matching version-2 nonce ACK** may be
classified as attachment fragments, bounded to at most 32 lines, 64 KiB and
750 ms from the first observed RX callback. They remain counted/reported and
are not described as packet loss. No matching ACK, an exceeded bound, oversized
lines, or truncation leaves the errors unqualified and failing. Every malformed
line after that ACK fails, including one later in the same RX chunk. This raw
acceptance window is stricter than the legacy parsed test's pre-hello tolerance.
An unfinished non-whitespace line at EOF fails.
The initial CAN+32-hex challenge is recognized only on TX; it is not a JSON
frame. Its matching version-2 nonce acknowledgement must precede the first
normal command. The paired hello capabilities must still be present. A wire line longer than 16,384 bytes is discarded through LF and fails the gate;
a large chunk containing many short lines is allowed.

`host_monotonic_ns` is the record/handling timestamp. Optional
`data.received_monotonic_ns` is the driver-observer callback timestamp on the **same
host monotonic clock**, no later than the record timestamp. For fragmented
frames the final chunk timestamp marks line completion. Missing callback
measurements block handling-age acceptance. For TX this remains a host capture
point, not proof of device delivery. This is not an actual UART arrival or
sensor measurement timestamp. Unknown USB/UART and SerialInputOutputManager
queue delay before the observer callback is unmeasured; near-zero callback-to-
recorder handling delay cannot clear absolute physical feedback age. The report
keeps `absolute_feedback_age_verified=false` and `driver_queue_latency_measured=false`.
There is no absolute firmware-to-host age
measurement without clock synchronization; it remains JSON null.

`RawUsbCaptureRecorder` writes `session_end.data.transport_teardown`, which
may record `{"quiesced":true,"host_write_outcome":"HOST_WRITE_COMPLETED"}`
only after the instrumentation actually observes both transport futures. The
parallel parsed `RunSessionRecorder` file does not contain this field. Missing evidence blocks the check; failed/timed
out outcomes fail it. Host completion still does not prove a received safety
command or physical torque-register state.

## Checks and interpretation

- Firmware: opening/closing hello version label; monitor capability; 20 ms
  feedback contract; explicitly unarmed monitor states; no recorded motor,
  torque-enable, stand, program, configuration, arm or ordinary policy commands.
  The exact all-servo torque-disable teardown packet is allowed
- UART: four counters in order FIFO overflow, buffer full, frame error,
  parity error. Hellos must bracket sampled states. Compute differences from
  cumulative counts; regressions/reset or positive deltas fail
- Coverage: at least 1,250 states; first 50 warm up aggregate timing, but startup
  sample stalls/errors remain failures. This is about 25 seconds, not endurance
- Sequence: no feedback tick gaps/repeats/regressions. Missing ticks infer absent
  recorded feedback; the capture cannot attribute that to USB vs logger/device
- TX/ACK: unique increasing uint32 monitor seq and twelve numeric target keys;
  reject unknown/future/regressed ACKs, stalls over 120 ms, and first matching
  ACK over 120 ms after the TX record. Require the final command's seq to appear
  or BLOCK. Firmware reports the latest accepted seq; intermediate unobserved
  ACKs are not proven lost commands. The report exposes repeated/same-ACK run
  lengths, latest-sent minus ACK lag, and oldest-unacknowledged-record age.
  Fresh advancing feedback ticks do not excuse an ACK stalled over 120 ms. Parsed TX timestamps include queueing;
  raw TX timestamps mark the native write attempt, still not physical delivery
- Timing: firmware/recorded host mean 49.5–50.5 Hz; firmware sample p99 <=25 ms,
  every adjacent sample interval <=40 ms and positive, including startup;
  uint32 firmware millis wrap is supported. Cumulative missed periods must be 0
- Feedback handling age: every same-host callback-to-record age <=100 ms;
  this measures recorder-admission delay after a driver callback, not absolute
  sensor age, USB/driver queue latency or age at inference use
- Relative sample/callback drift: compare host elapsed time to unwrapped firmware
  elapsed time; >100 ms range fails. This detects changing delay/clock trouble,
  but cannot exclude a constant pre-existing queue delay
- Servos: unique IDs1..12, finite angles, complete feedback and zero status
  errors on every state; >=99% complete aligned current frames. Missing currents
  remain missing. One signed current step is 6.5 mA
- IMU: availability, finite 3-axis values, stationary acceleration norm
  500..1500 mg. This is plausibility only, not calibration or orientation proof
- Firmware processing: feedback/current/frame timing counters present, frame
  below 20,000 us. These exclude native host writes, inference and camera work

Thresholds are intentionally fixed; there is no waive-missing/pass-anyway flag.
A synthetic-marked session stays synthetic even with `--evidence-kind
operator-capture`. The origin of arbitrary unmarked input remains an operator
assertion, not cryptographic proof.

## Offline regression and reproducible example

```sh
python -B -m unittest -v deployment_readiness.test_hardware_acceptance
python -B -m deployment_readiness.hardware_acceptance deployment_readiness/examples/synthetic-monitor.jsonl --target dog12 --evidence-kind synthetic
```

The second command intentionally exits 2/BLOCKED. It contains only three
**authored synthetic** frames and cannot establish physical USB health.
`examples/synthetic-monitor-report.json` is its small illustrative report;
the CLI additionally hashes the input path and bytes. Tests exercise fragmented,
coalesced, padded, truncated, malformed, oversized and invalid UTF-8 streams,
strict JSON, missing data, tick/ACK/counter resets, firmware clock wrap, startup
stalls, current dropouts, IMU errors, capture identity and read-only CLI behavior.
Synthetic clean-path unit tests may verify internal PASS logic; that is parser
regression evidence only. No device or backend is accessed by these tests. Eight Kotlin helper tests
exercise exact-byte copies, provenance, timestamps, bounded queue overflow,
finalization timeout, storage/size failures and teardown preservation. The host
Kotlin runner also type-checks this selected instrumentation source against
throwing signature-only Android/AndroidX stubs and **never executes it**. That
is not an Android SDK build, API compatibility check, or physical qualification.
