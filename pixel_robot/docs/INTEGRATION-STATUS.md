# V4 integration status

Work is isolated in `C:\Users\Leo\Code Projects\robot-v4-review`, branch
`review/robot-v4`, based on `27878e11f8d1c023fe9e1c1f62b4a3e18ba1f869`.
Archive checksums passed. Applying the cumulative V4 patch produced the exact
handoff tree `a314e14f06b38d66daa3c5055cbc0c18e6296529` before integration edits.
The Pixel Robot, Robot and Robot-delivery checkouts and their existing work
were preserved. Policy assets were not replaced or promoted.

## Android and local model

The debug app and test APK build with the existing SDK and Java 17:

```powershell
cd pixel_robot
.\gradlew.bat -ProbotReviewInstall=true test assembleDebug assembleAndroidTest
```

The review flag installs `com.leo.pixelrobot.review` alongside the existing app,
preserving its signing identity and private calibration. The flag is optional;
default builds retain the original application ID. No SDK licenses were accepted.
Leo approved installation and launch for non-actuating tests on Pixel 10 serial
`66270DLCR002RH`. The phone was connected to the computer, not the robot.

Six selected device tests passed: service lifecycle, loaded assets/background
cancellation, replacement/Stop fencing, independent admitted-preview expiry,
60 installed-policy frames and ONNX parity. These ran without opening a robot
transport. The native screen was inspected: actor ready, camera off, disconnected
robot, disabled Stand and zero command values. Device evidence is retained under
`validation/results/device-001/`.

The real served shadow panel was exercised through the collaborative browser.
Bonsai's slow response produced `timed_out` / `provider_result_deadline_elapsed`
at the eight-second cutoff, with physical execution and stop confirmation false.
Browser DOM checks passed; browser screenshots failed, so full browser visual
acceptance remains unverified.

The existing on-phone model remains selected:

- Endpoint: `http://127.0.0.1:18081/v1`
- Model: `/data/local/tmp/pixel-llm/models/Ternary-Bonsai-2-27B-PQ2_0.gguf`
- Strict stop, body-velocity and clarification responses took 70.4, 61.9 and
  32.5 seconds respectively. All were valid; none met the app's eight-second
  deadline. Three examples do not qualify a model or prove task completion.

Raw requests, completions and timings are in
`validation/results/local-provider-001/`. Leo chose to keep Bonsai and defer
model comparison. The deadline is unchanged; slow results cannot actuate or
renew an expired request. Literal phone-loopback HTTP is allowed with explicit
per-request consent; HTTP DNS names and LAN endpoints remain rejected.

## Software and firmware evidence

The complete offline suite and exact final source/dependency fingerprints are
recorded in `validation/results/final-offline-003/latest.json` and its cycle
artifacts. It includes the real Kotlin seams, browser-script tests, strict
decoder/provider checks, BAML generation consistency and CPU actor/runtime
parity. CRLF handling was corrected without relaxing substantive source checks.

Independent operator leases, final-write fencing and the bounded USB writer are
implemented and host-tested; see [HOST-TESTS.md](HOST-TESTS.md). No shadow-to-motion
route was added. The actor and calibration remain V22 epoch 3750, 428 inputs,
`cad_drives_v1`, 2 Mbaud, unpromoted. The separate older Pixel Robot controller
uses 180 inputs and 921600 baud; these bundles must not be interchanged.

Firmware host checks passed and the pinned ESP32 compile-only build succeeded.
Evidence and binary hash are under `validation/results/firmware-host-001/`.
No firmware upload, OTA, arming, torque-enable or physical movement was attempted.

## Remaining operator acceptance

Before a robot trial, verify the exact firmware, servo/calibration mapping and
bundle identity with motor outputs disabled. Measure acknowledgement/readback,
50 Hz timing, stop/watchdog behavior, service destruction and USB fault recovery.
Then obtain Leo's explicit physical-trial approval and observe the bounded trial.
Driver completion alone never proves firmware receipt or torque-off.

Simulator acceptance and the recorded physical-capture gate remain external
checks. The named capture file is unavailable in the supplied checkouts. No
promotion or hardware acceptance is claimed. After approval, verify standalone
startup and recovery with the computer unplugged, as the intended robot setup
requires. Keep physical controls idle until that session.
