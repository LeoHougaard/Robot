# Delivery test procedure

The robot and Pixel mounting match the August 29 capture. Leo starts all
physical motion. Tonight's software work cannot establish that the robot walks
or that the complete powered mechanism sustains 50 Hz.

## Installable software

The isolated laptop checkout is `C:\Users\leo\Code Projects\Robot-sim-to-real`.
The Android debug APK is under `pixel_robot\app\build\outputs\apk\debug`.
Firmware 0.1.15 is under
`robot_dog\firmware\robot-dog-control\.pio\build\esp32dev\firmware.bin`.
Before flashing, switch the Pixel USB cable to the ESP32 desktop computer. Keep
the servo power off. Identify the board's current serial port; do not assume an
earlier COM number:

```powershell
python -m platformio device list
python -m platformio run -e esp32dev --target upload --upload-port COM_REPLACE
```

Run these commands inside `robot_dog\firmware\robot-dog-control`. The firmware
keeps existing calibration in NVS and disables torque on boot. Confirm all
twelve torque registers are off before handling unsupported legs.

After the desktop flash and its read-only verification, unplug the board from
the desktop and switch the cable to the Pixel through the OTG adapter. Do not
leave the Pixel connected during desktop flashing.

## Read-only rate test

Connect the Pixel directly to the ESP32, power the servo bus, and grant Pixel
Robot USB permission. The robot must already have torque off. Keep its legs
supported. This diagnostic refuses to take over a held pose and never sends
an arm, torque-enable, position-write, or configuration command.

Install the app and instrumentation APKs without clearing app data. Discover
the current wireless ADB endpoint with mDNS after each reboot; the previously
observed `10.1.39.188:41395` endpoint is evidence only and must not be reused
as a fixed address:

```powershell
adb mdns services
adb connect PHONE_MDNS_ENDPOINT
```

Stop the app to give
the diagnostic sole ownership of USB, then run:

```powershell
adb -s PHONE_ADDRESS shell am force-stop com.leo.pixelrobot
adb -s PHONE_ADDRESS shell am instrument -w -e robot_hardware torque_off -e class com.leo.pixelrobot.policy.MotorDisabledTransportTest com.leo.pixelrobot.test/androidx.test.runner.AndroidJUnitRunner
adb -s PHONE_ADDRESS shell run-as com.leo.pixelrobot cat files/motor-disabled-transport-result.json
```

The test checks firmware capability, verifies disabled torque through firmware,
and processes 1,250 real sensor frames with the installed actor, the production
sensor/history/action transforms, JSON encoding, USB and asynchronous recording.
The first 50 frames warm up the path. It requires 49.5–50.5 Hz at firmware and
host, no missed firmware deadlines or skipped feedback ticks, sample p99 at
most 25 ms and maximum 40 ms, compute p99 below 10 ms, and at least 99% complete
current frames. Incomplete recording fails the test. Results and raw diagnostic
sessions stay in the app's `files/transport_diagnostics` directory.

The diagnostic commands are `policy_monitor` and `policy_monitor_frame`.
Firmware checks the same target packet shape and limits but discards those
targets. Normal motion/configuration commands are rejected while monitoring;
`policy_disarm` stops it, and it ends automatically after at most 60 seconds.
The rate test excludes the motor-position broadcast and motion-dependent load.
Measure those in Leo's supervised run before claiming powered 50 Hz operation.

Repeat the read-only test after a board power cycle and USB reconnection.
A 25-second passing test is an initial rate check, not a long thermal/endurance
test. Camera operation is outside this diagnostic; leave it off for the initial
walking test and evaluate it separately before enabling it during control.

## Candidate epoch2750 diagnostic (test APK only)

The installed production app remains bound to its existing policy bundle. The test-only
`CandidateEpoch2750MotorDisabledTransportTest` binds the explicit `candidate_epoch2750`
asset bundle: 428-input CAD V22 actor, production `PolicyFrameSession`, exact CAD stride
reference, profile, calibration bytes, portable weights and ONNX manifest. It requires
`policy_candidate=epoch2750` and `robot_hardware=torque_off`; it never enables torque or
writes motor targets. The bundle is candidate evidence and has no deployment approval. Candidate assets are read from the instrumentation APK context, so the offline packaging/parity check is `CandidateEpoch2750AssetSmokeTest`; it opens the ONNX actor, validates metadata, reference/calibration bindings, and runs the packaged reference vectors without USB or motor access.

Build the debug app and instrumentation APK in the laptop checkout:

```powershell
Set-Location 'C:\Users\leo\Code Projects\Robot-sim-to-real\pixel_robot'
$env:JAVA_HOME = "C:\Program Files\Android\Android Studio\jbr"
$env:ANDROID_HOME = "C:\Users\leo\AppData\Local\Android\Sdk"
.\gradlew.bat :app:testDebugUnitTest :app:assembleDebug :app:assembleDebugAndroidTest
```

After Leo has identified the board, confirmed torque off, and installed the test APKs, run
this diagnostic explicitly; the normal app policy is not replaced:

```powershell
adb -s PHONE_ADDRESS shell am force-stop com.leo.pixelrobot
adb -s PHONE_ADDRESS shell am instrument -w -e robot_hardware torque_off -e policy_candidate epoch2750 -e class com.leo.pixelrobot.policy.CandidateEpoch2750MotorDisabledTransportTest com.leo.pixelrobot.test/androidx.test.runner.AndroidJUnitRunner
```

For an offline emulator or board with no USB test, run only the asset check:

```powershell
adb -s PHONE_ADDRESS shell am instrument -w -e class com.leo.pixelrobot.policy.CandidateEpoch2750AssetSmokeTest com.leo.pixelrobot.test/androidx.test.runner.AndroidJUnitRunner
```

Before any physical use, Leo must confirm the candidate bundle hash, board calibration
against `candidate_epoch2750/assembly-four-leg-linkage-12dof.calibration.json`, loaded
reference hash, and measured 20 ms feedback timing. The corrected Isaac ground and
SDF fidelity report passed (`sdf_fidelity_report_sha256` is retained in the delivery
manifest), while physical calibration and loaded timing remain pending. The existing
production live stride guard remains in force.

## Before Leo starts walking

Check the displayed app/firmware versions, calibration source and exact policy
hash against the retained test evidence. The app uses metadata to bound the
controls. V20 support alone does not mean a V20 policy was promoted.
Use the known battery, mounting and payload, start recording, and have Leo
perform supported standing followed by brief floor start/stop/turn trials.
Keep logs and video together. Stop and inspect sustained tracking error,
wrong-direction travel, dragging feet or an unexpected lean before increasing
duration or terrain difficulty. Carpet and small uneven surfaces follow stable
floor trials; outdoor slopes and steps remain later work.
