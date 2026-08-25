# Pixel Robot

Pixel Robot moves the robot dog's host-side control loop from a PC or
Raspberry Pi to one Android application on a Pixel phone. The ESP32 remains the
real-time actuator and safety controller.

This is a deliberately trimmed OpenBot adaptation: it keeps the proven
phone/USB/camera/local-control shape, but does not carry OpenBot's car UI,
Firebase, Drive, ARCore, RTSP, TensorFlow Lite models, or legacy WebRTC bundle.

## Current hardware and policy contract

- Robot board: Waveshare ESP32 General Driver-class board, built as
  `esp32dev`; the previously used Windows port is a CP210x USB-UART bridge
  (`VID 10C4`, `PID EA60`).
- USB protocol: newline-delimited JSON at 921600 baud. Firmware output is
  padded to 64-byte USB packets before CRLF.
- Safety: firmware boot disables torque; learned control requires explicit
  arming, all 12 servo/IMU feedback, monotonic sequence numbers, <=6 degree
  target steps, and a 120 ms stale-frame watchdog.
- Policy: restricted Robust Isaac Lab/RL-Games test actor, four 45-value frames
  (180 total), 12 actions, 50 Hz, CPU ONNX Runtime, profile
  `assembly-four-leg-linkage-12dof`.
- Test command envelope: forward 0.00-0.22 m/s, lateral 0.00 m/s, yaw -0.25
  to +0.25 rad/s. Reverse, strafe, and in-place-turn behavior have not passed
  the Goal screen and remain outside this test build.

See [docs/AUDIT.md](docs/AUDIT.md) for the source-backed audit and
[docs/USB-C.md](docs/USB-C.md) for the direct-cable test.

## Build

Open the project in a current Android Studio with Android SDK 37 installed, or
run:

```powershell
.\gradlew.bat test assembleDebug
python tools\check_elf_alignment.py app\build\outputs\apk\debug\app-debug.apk
```

The Pixel stays connected to the ESP32 over USB-C. Pair Android Studio once
through **Developer options > Wireless debugging**, then deploy over Wi-Fi.
Camera preview and analysis are off by default. The camera switch binds the
Pixel's rear physical ultra-wide lens, shown in the app as the 0.5× camera.

## Install the Pixel demo

Pair the Pixel once through **Developer options > Wireless debugging**. The
wireless connection leaves the phone's USB-C port free for the ESP32. Then run:

```powershell
.\Install-PixelRobotDemo.ps1
```

The script runs unit tests, builds the debug and instrumentation APKs, checks
every native library for Pixel-compatible 16 KB page alignment, installs
without clearing the saved calibration, runs the ONNX parity and safe-lifecycle
tests on the Pixel, forwards the optional browser control page, and opens the
app. A failed on-device test prevents the demo from launching.

The whole floor demo is available on the Pixel:

1. Connect the independently powered robot controller and accept Android's USB
   permission prompt.
2. Put the robot on the floor in a clear area and tap **STAND**. The app reads
   the current pose and interpolates all joints to the bundled stand target in
   at most 24 synchronized steps. It holds the pose at 100 percent torque.
3. Choose forward speed and yaw, then tap **Run trained policy**.
   Policy motion uses 100 percent of the configured servo torque limit.
4. Tap **STOP + TORQUE OFF** before touching the robot. The same stop action is
   in the foreground notification. Swiping away the app also requests stop and
   torque-off.

Use `-BuildOnly` to prepare the APKs without a connected Pixel. By default the
installer refuses wired ADB because that port is needed for the ESP32 during
the demo. `-SkipDeviceTests` exists for troubleshooting, not a physical demo.

## Shared-browser test

The app serves a control page only on the Pixel loopback interface. Forward it
through the paired wireless ADB connection so the page is not exposed to the
LAN:

```powershell
adb forward tcp:8767 tcp:8767
```

Open `http://127.0.0.1:8767/`. The page exposes the same stand, trained-policy,
and stop controls as the native screen, limited to the actor's supported forward
and yaw ranges. A test runs until Stop sends policy disarm and all-servo
torque-off.
Hiding or closing the page also requests stop while a test is active. Repeat the
ADB forwarding command after restarting ADB or the computer.

Policy inertial observations come only from the ESP32 board's QMI8658. With the
board component-side up and both USB-C ports facing the robot's rear, the app
locks the sensor-to-body transform to `[sensor Y, -sensor X, sensor Z]` and
rejects a different calibration. The Pixel phone IMU is not used.

## Safety boundary

Floor operation and falls are part of the robot's intended mechanical boundary.
Keep people, pets, cables, and fragile objects outside the fall zone. Use the
Pixel's **STOP + TORQUE OFF** button whenever motion should end or before touching
the robot. USB reconnect checks, servo feedback limits, command-step limits, and
the firmware watchdog remain active during operation.
