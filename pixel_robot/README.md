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
- USB protocol: newline-delimited JSON at 2,000,000 baud. Firmware output is
  padded to 64-byte USB packets before CRLF.
- Safety: firmware boot disables torque; learned control requires explicit
  arming, all 12 servo/IMU feedback, monotonic sequence numbers, <=6 degree
  target steps, and a 120 ms stale-frame watchdog.
- Servo battery: the ST3215 bus reports the independently powered 2S LiPo.
  The Pixel app, browser page, foreground notification, and run data warn at
  7.0 V and show a critical warning at 6.6 V. These warnings do not block
  standing or policy control.
- Policy: restricted Robust Isaac Lab/RL-Games test actor, four 45-value frames
  (180 total), 12 actions, 50 Hz target, CPU ONNX Runtime, profile
  `assembly-four-leg-linkage-12dof`.
- Test command envelope: forward 0.00-0.22 m/s, lateral 0.00 m/s, yaw -0.25
  to +0.25 rad/s. Reverse, strafe, and in-place-turn behavior have not passed
  the Goal screen and remain outside this test build.

See [docs/AUDIT.md](docs/AUDIT.md) for the source-backed audit and
[docs/USB-C.md](docs/USB-C.md) for the direct-cable test. The run-data format
and the measurements it contains are documented in
[docs/RUN-DATA.md](docs/RUN-DATA.md).

## Build

Open the project in a current Android Studio with Android SDK 37 installed, or
run:

```powershell
.\gradlew.bat test assembleDebug
python tools\check_elf_alignment.py app\build\outputs\apk\debug\app-debug.apk
```

The unit suite includes a fake ESP32 stand-protocol state machine. It runs the
same uploader used by `PolicyController` through full 24-step trajectories,
100 deterministic trajectory variants, rejected records, lost acknowledgements,
and truncated USB JSON. Failure cases must stop before `program_start`.

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
   12-24 synchronized, eased steps at 80 ms per step. The per-joint change is
   capped at 3 degrees, and all joints remain on the same trajectory clock. The
   app uploads each step as a small acknowledged record, then starts the whole
   trajectory on the ESP32. A partial upload never starts motion. It holds the
   pose at 100 percent torque.
3. Choose a servo ID under **Servo load sensing**. While the robot holds the
   stand pose, the app reads that ID every 250 ms. During trained-policy motion,
   firmware reads the critical position/speed block and a separate, non-fatal
   current block for all 12 servos on every policy frame. The app shows the
   selected ID's current, estimated joint torque, and position. Full load,
   voltage, temperature, motion, and status fields remain available from the
   selected-ID idle read. The ST3215 does not report calibrated foot force.
4. Choose forward speed and yaw, then tap **Run trained policy**.
   Policy motion uses 100 percent of the configured servo torque limit.
5. Tap **STOP + TORQUE OFF** before touching the robot. The same stop action is
   in the foreground notification. Swiping away the app also requests stop and
   torque-off.

Tap **Record** before or during a test. Recording continues through stand and
policy operation until **Stop recording** or stop/disarm. **Share last run
data** exports the JSONL. **Share training capture** exports one verified ZIP
with that run, the exact deployed ONNX actor, its metadata/reference, and the
effective calibration. The browser exposes both downloads. An interrupted app
process leaves a recoverable file which is exposed after the next recording
starts.

Inspect, fit, and graph a downloaded recording with the dependency-free tools:

```powershell
python tools\inspect_run_data.py "<robot-run.jsonl>"
python tools\fit_sim_from_run_data.py "<robot-run.jsonl-or-training-capture.zip>"
python tools\plot_run_data.py "<robot-run.jsonl>" --servo-id 2
```

The one-command path accepts the training-capture ZIP directly, verifies every
hash, writes the summary and simulation fit, creates all four graphs, and can
enforce the physical 50 Hz gate:

```powershell
python tools\analyze_training_capture.py "<training-capture.zip>" --servo-id 2 --require-50hz
```

When operator handling needs to be excluded, keep the downloaded files intact
and create a provenance-bearing policy-time slice before fitting:

```powershell
python tools\trim_training_capture.py "<training-capture.zip>" --start-seconds 0 --end-seconds 51.0 --output-run "<trimmed.jsonl>" --output-capture "<trimmed-training-capture.zip>" --reason "operator handling removed"
```

The graph command writes SVG plots for all-servo current, the selected servo's
current and position tracking, and control timing. The one-command analyzer also
writes a simple `index.html` page containing all four graphs. Full recordings
and generated plots stay under `pixel_robot/run_data/` and remain outside Git.

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
stop, and run-download controls as the native screen, limited to the actor's
supported forward and yaw ranges. A test runs until Stop sends policy disarm
and all-servo torque-off.
Hiding or closing the page also requests stop while a test is active. Repeat the
ADB forwarding command after restarting ADB or the computer.

The voltage monitor reads the servo rail while policy control and stand playback
are idle so it cannot interfere with servo feedback timing. During motion the UI
clearly labels the cached value as the last idle reading. Firmware takes one
fresh reading during policy preflight, before arming the policy loop. Recharge at
the 7.0 V warning rather than waiting for the 6.6 V critical warning.

The selected-servo load monitor polls one ID every 250 ms while the completed
stand pose is held. During policy control, firmware keeps the proven synchronized
four-byte position/speed read as the safety-critical transaction and performs a
separate synchronized two-byte current read across all 12 servos. A missing
current sample is retained as telemetry loss and cannot stop policy control;
missing critical position feedback still triggers the three-frame firmware
safety limit. The compact response and 2 Mbaud host link reduce transport time.
The recorded firmware sample interval is the authority for the achieved rate.
The Android app computes torque for all IDs and displays the selected one.

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
