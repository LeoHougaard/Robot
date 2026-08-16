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
- Policy: promoted Isaac Lab/RL-Games actor, four 45-value frames (180 total),
  12 actions, 50 Hz, CPU ONNX Runtime, profile `assembly-1-12dof`.
- Validated command envelope: forward 0.00-0.18 m/s, lateral 0.00 m/s, yaw
  -0.25 to +0.25 rad/s.

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

## Safety boundary

Do not test on the floor from this application until USB reconnect and
watchdog behavior have passed a 30-minute motors-disabled test and the policy
actor matches the checked-in desktop reference vectors. Initial learned-policy
tests must keep the robot securely suspended.
