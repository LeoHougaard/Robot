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

## Actual board flash, September 6

Firmware 0.1.15 was written to the connected ESP32-D0WD-V3 on COM3,
active app0 at 0x10000. Its SHA256 is
`9c01e6b931c2a1dc01ef8724da5d1a5a4082e14895930e8697adc00061556971`.
Device-side MD5 and complete host readback matched the firmware. The partition
map, NVS calibration and OTA selection were byte-for-byte unchanged. The old
application and settings are backed up privately outside Git.

Bulk readback had intermittent checksum errors at 460800 and 230400 baud;
16 KiB reads at 115200 completed without retries. The runtime hello at 2 Mbps
was not received after software reset. Verify startup after reconnecting to the
Pixel before the candidate torque-off transport test. Firmware flashing is
complete; physical 50 Hz and walking remain unverified.

## Immediate startup failure diagnosed

The first user test on firmware 0.1.15 stopped at sequence 1 with
`invalid or stale firmware sample interval: 1289 ms`. Inference was 0.906 ms.
The phone was connected at 2 Mbps with complete servo feedback and 7.4 V.
Firmware tick 0 samples the IMU before blocking servo arming/configuration,
so its timestamp cannot initialize the running policy's sensor history.
The controller now keeps tick 0 only as the held-pose acknowledgement and
waits for a fresh later tick before its first observation and target. Existing
stale-feedback limits remain enforced. A sensor regression reproduces the
1,289 ms failure and verifies consecutive fresh 20 ms samples.

The phone now queues 64 endpoint-sized USB reads to receive data while its
listener processes earlier packets. The candidate diagnostic synchronizes its
hello handshake when opening a UART already streaming periodic messages;
malformed packets after the handshake still fail the diagnostic.

The normal app is being changed from the previous epoch 1700 actor to the
explicitly allowlisted epoch 2750 CAD candidate. Its command limits are
+/-0.04 m/s forward, +/-0.02 m/s lateral and +/-0.1 rad/s yaw, with no posture
commands. Runtime status identifies the epoch and actor hash. Hardware rate
verification and installation of this final bundle are still pending here.

### Candidate installed for Leo's supervised trial

The final laptop-signed app built from `a1566be` is installed on the Pixel.
APK SHA256: `c4282b69e2e339a45697e52b7c3d2e5388995e2a63551cd871ea1909eeb96681`.
The live status reports epoch 2750, `current_body_v22_428`, weights
`95b81a0056686a9d8de82d503e0a34072c45dfa2ddd3ceccfa738c69d969ea3c`,
firmware 0.1.15, Actor ready, disarmed, and 7.4 V.

All twelve saved physical zero angles, signs and limits, plus the IMU mapping,
match the candidate calibration exactly. The app calibration now uses CAD drive
coordinates, removing the four legacy parent terms. Its exact SHA256 is
`6d5cccade24a54c8ff98e7e194f41af676c9f6268be15e44d9293b649090bfa6`;
the previous calibration remains in app storage with `.legacy-before-v22` suffix.

The motor-disabled diagnostic did not complete: its latest attempt timed out
waiting for hello. The normal app subsequently connected and loaded the exact
candidate. Leo requested an immediate trial without further diagnostic retries.
This installation is a supervised candidate trial, not a passed physical 50 Hz
or walking gate. Timing guards and the firmware watchdog remain active. Leo
starts recording and all physical motion; no policy or motor run was started
by an agent. Keep the first supported start and floor movement brief.

### Fixed UI crash with the restricted candidate

The app previously classified every non-V2 actor as supporting posture, then
set each posture slider to a zero-width range for this candidate. Material
Slider threw `valueFrom(0.0) must be smaller than valueTo(0.0)` while drawing.
Posture availability now follows the actual command limits. Fixed commands
stay disabled and retain valid drawing ranges.

Commit `71e96b3` is installed. APK SHA256 is
`0beab9ce6e310f3d00269f81eb6edbb2da33e2a0f677930249092219d3a57e9e`.
The actual Pixel screen was visually inspected after a cold launch and again
after returning from Home. It stays open, shows USB/Actor ready, and displays
disabled posture sliders. Live status confirms epoch 2750 and disarmed state.
The screenshot and status are retained in `desktop-pixel-evidence` outside Git.
