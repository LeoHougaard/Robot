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
Camera preview and analysis are off by default; enable them explicitly in the
app only when a camera task is needed.

## Capture the simulation start pose

The app can use a physical stand to register the exact simulated start pose
without commanding a joint target:

1. Seat the robot in a stand that fixes the chassis, feet, knee folds, and hip
   directions at the simulation start pose.
2. Power the ESP32 and the 7.4 V servo rail, then connect the Pixel over USB.
3. Check **Robot is seated in the exact simulation start-pose stand** and tap
   **Capture current positions as policy zero**.
4. The app disables torque, reads every servo's physical torque-enable register,
   averages five encoder samples, and rejects motion over 1 degree.
5. The app synchronizes and reads back the same zero and safe limits on the
   ESP32, then stores them in private app storage. A failed Pixel save restores
   the prior ESP32 calibration. Reinstalling with `-r` preserves the Pixel
   override; clearing app data removes it.

Before every policy start, the app repeats the torque-off check and refuses to
arm if any joint is more than 8 degrees from the captured reference pose.

## Shared-browser suspended test

The app serves a control page only on the Pixel loopback interface. Forward it
through the paired wireless ADB connection so the page is not exposed to the
LAN:

```powershell
adb forward tcp:8767 tcp:8767
```

Open `http://127.0.0.1:8767/`. The page requires confirmation that the robot is
secured and exposes only the actor's validated forward and yaw ranges. **Capture
this pose as zero** verifies torque-off, averages five stable readings from every
servo, synchronizes and verifies the ESP32 limits, and saves the same reference
in private Pixel storage. **Stand at captured zero** verifies torque-off feedback,
refuses a pose more than 45 degrees away, and returns all joints in steps of at
most 3 degrees before holding the captured position at the firmware's 25 percent
commissioning torque limit. A test runs until Stop sends policy disarm and
all-servo torque-off.
Hiding or closing the page also requests stop while a test is active. Repeat the
ADB forwarding command after restarting ADB or the computer.

Policy inertial observations come only from the ESP32 board's QMI8658. With the
board component-side up and both USB-C ports facing the robot's rear, the app
locks the sensor-to-body transform to `[sensor Y, -sensor X, sensor Z]` and
rejects a different calibration. The Pixel phone IMU is not used.

## Safety boundary

Do not test on the floor from this application until USB reconnect and
watchdog behavior have passed a 30-minute motors-disabled test and the policy
actor matches the checked-in desktop reference vectors. Initial learned-policy
tests must keep the robot securely suspended.
