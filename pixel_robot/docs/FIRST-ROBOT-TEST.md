# First robot test: AI text to a bounded command

This guide targets the checked-in **12-servo Pixel / ESP32 robot dog**, not the
four-wheel rover. A rover needs its own actuator mapping, feedback contract and
commissioning evidence; the dog's ONNX actor cannot drive four wheels.

## What is ready to test first

Start with the software checks, then text-only preview, then the explicit
torque-disabled USB diagnostic. These do not establish walking performance.
Physical AI commands are a later stage, after the exact installed bundle and
transport have been qualified. The shipped bundle still declares
`promoted:false` and `physical_transport_verified:false`; the physical text panel
must remain blocked with those assets. Do not change these booleans merely to
clear the UI.

The existing manually started experimental policy path is separate. It is not
automatically started by an AI result or by opening the control page.
The app source candidate is `0.2.11-ai-test` (version code 13); this identifies
the revised source and does not mean an APK has been built or installed.

## 1. Reproduce the software evidence

Use the pinned environment in [validation/README.md](../../validation/README.md).
From the repository root:

```sh
python -B -m deployment_readiness --format json
python -m validation --profile offline --base-ref 27878e11f8d1c023fe9e1c1f62b4a3e18ba1f869
```

The preflight's default all-scope result intentionally remains BLOCKED while
physical evidence is absent. Read its individual checks; an offline PASS is
not permission to move. Retain source-bound reports with the actual installed
app, firmware and policy hashes. Run validation again after merging changes.

Build the real Android app and instrumentation APK with the required SDK before
installation. Host Kotlin tests and authored browser stubs do not substitute
for this build or for a Pixel/WebView test:

```sh
cd pixel_robot
./gradlew :app:testDebugUnitTest :app:assembleDebug :app:assembleDebugAndroidTest
```

Use `gradlew.bat` on Windows. See the current build files for toolchain versions.
Do not clear application data as part of installation: existing calibration
must be reviewed and preserved.

## 2. Test the real provider without motor authority

Follow [SHADOW-PREVIEW.md](SHADOW-PREVIEW.md). Enter your exact authorized HTTPS
OpenAI-compatible endpoint and model, explicitly consent to sending the text,
and submit a single request. The current connector does not store credentials,
follow redirects, or guess a model. An authenticated provider needs a separately
implemented connection; putting a secret in a URL or prompt is not a workaround.

Check normal intent, stop, ambiguity, unsupported requests, malformed provider
output, timeouts and cancellation. The preview must never start a policy, arm
servos or become a physical command. Record which endpoint/model was actually
tested; synthetic response tests are not a real-provider test.

## 3. Verify the installed transport with torque disabled

The new app/firmware transport is a paired update. The candidate firmware is
`0.1.18-transportguard`; it has not been flashed by this software work. Install
and verify firmware with motor power disconnected before connecting the new
app. Its initial parser-abort handshake deliberately rejects older firmware.
Do not bypass that check: completing an old buffered command with a newline was
one of the transport hazards this revision addresses.
If an older board is rejected, disconnect motor power and install/reset the
paired firmware before trying another client. A rejected link receives no
newline; the app cannot prove that an older firmware's preexisting input buffer
has been cleared.

Use the **epoch3750** monitor procedure in [DELIVERY-TEST.md](DELIVERY-TEST.md),
not its older epoch2750 historical section. Confirm the physical robot matches
the installed calibration, all twelve IDs are correct, and the mechanism is
supported before powering the servo bus. Identify the current device/ADB
endpoint instead of reusing historical addresses or COM numbers.

The diagnostic requires explicit `robot_hardware=torque_off` and
`policy_candidate=epoch3750` arguments. It evaluates sensor feedback, inference
and transport without enabling torque or sending position targets. The new
transport teardown can request stop, disarm and torque disable. A completed host
write is not a firmware acknowledgement or proof that the motors stopped.

Preserve the result JSON and raw session evidence. Repeat after a board power
cycle and USB disconnect/reconnect. Treat missing bytes, skipped ticks, stale
feedback, missing servo replies, recorder overflow or unknown firmware identity
as a failed/incomplete commissioning step, not a reason to loosen the limits.
Motor-disabled timing does not measure motor-write load or walking stability.

## 4. Qualify the exact physical configuration

Leo operates all powered tests. Verify calibration and direction, supported
standing, stop behavior, link-loss behavior, and then short supervised movement
trials using the existing manual experimental controls. Keep an accessible
physical power disconnect and preserve logs alongside observations/video.

Qualification must identify the exact policy, effective calibration, firmware,
app, mounting and payload. Asset flags describe a reviewed decision supported
by that evidence; they cannot create it. Do not substitute synthetic capability
fixtures, the legacy 180-input Python actor, or a different checkpoint.

## 5. Test one physical text request

Only after qualification and with a supported physical test setup:

1. Select the exact endpoint, model and request text.
   Editing these fields after enabling physical control revokes the enable.
2. Manually start the reviewed policy with a neutral command and fresh complete
   feedback. Opening the panel does not start it.
3. After manual start, check text-transmission consent and the physical-risk
   acknowledgement, explicitly enable one physical text request in its separate
   panel, then submit the unchanged request. Manual control clears both consents.
4. Inspect the accepted named axes and bounded duration. The host, not the model,
   owns the request identity, deadline, bundle binding and execution permission.
5. Verify cancellation/expiry stops the operation and requires a fresh explicit
   manual start and enable. Do not assume zero commanded velocity means stopped.

Manual control, STOP, backgrounding, disconnect and service teardown revoke AI
authority. The final USB dispatch also checks operation ownership, lease validity
and feedback age. A software stop request is always distinct from physically
confirmed stop. Real device scheduling, USB behavior and loaded motor behavior
still need the corresponding physical tests.
