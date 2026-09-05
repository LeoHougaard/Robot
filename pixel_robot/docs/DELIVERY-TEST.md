# Delivery test procedure

The robot and Pixel mounting match the August 29 capture. Leo starts all
physical motion. Tonight's software work cannot establish that the robot walks
or that the complete powered mechanism sustains 50 Hz.

## Installable software

The isolated laptop checkout is `C:\Users\leo\Code Projects\Robot-sim-to-real`.
The Android debug APK is under `pixel_robot\app\build\outputs\apk\debug`.
Firmware 0.1.15 is under
`robot_dog\firmware\robot-dog-control\.pio\build\esp32dev\firmware.bin`.
Flash through the project's PlatformIO environment after identifying the
board's current serial port; do not assume the earlier COM number:

```powershell
python -m platformio device list
python -m platformio run -e esp32dev --target upload --upload-port COM_REPLACE
```

Run these commands inside `robot_dog\firmware\robot-dog-control`. The firmware
keeps existing calibration in NVS and disables torque on boot. Confirm all
twelve torque registers are off before handling unsupported legs.

## Read-only rate test

Connect the Pixel directly to the ESP32, power the servo bus, and grant Pixel
Robot USB permission. The robot must already have torque off. Keep its legs
supported. This diagnostic refuses to take over a held pose and never sends
an arm, torque-enable, position-write, or configuration command.

Install the app and instrumentation APKs without clearing app data. Use the
current wireless ADB address (it can change after reboot). Stop the app to give
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
