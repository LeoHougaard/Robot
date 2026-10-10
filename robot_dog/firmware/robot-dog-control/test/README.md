# Offline firmware validation

These checks compile source or run production handler/framing code against fake
I/O. They do not connect to a board, flash firmware, energize a motor, prove a
watchdog works on the device, or qualify hardware for motion.

## ESP32 compile only

Use a separate virtual environment with PlatformIO Core **6.1.18**, and copy
`src/`, `include/`, and `platformio.ini` to a temporary build directory. Keep all
PlatformIO packages and generated binaries outside the source checkout. For
example, from the repository root on Linux:

```sh
python -m venv /tmp/robot-firmware-venv
/tmp/robot-firmware-venv/bin/pip install platformio==6.1.18
mkdir -p /tmp/robot-firmware-build
cp -R robot_dog/firmware/robot-dog-control/src \
  robot_dog/firmware/robot-dog-control/include \
  robot_dog/firmware/robot-dog-control/platformio.ini /tmp/robot-firmware-build/
PLATFORMIO_CORE_DIR=/tmp/robot-platformio \
  /tmp/robot-firmware-venv/bin/pio run -d /tmp/robot-firmware-build \
  -c "$PWD/robot_dog/firmware/robot-dog-control/test/platformio.compile-only.ini" \
  -e esp32dev
```

Dependency installation requires the official PyPI and PlatformIO registries.
Once the exact packages are cached, the build itself needs no robot or backend.
Do not add upload, uploadfs, OTA or device-monitor targets to these commands.

The production `platformio.ini` remains unchanged and uses floating dependency
ranges. The separate `platformio.compile-only.ini` pins the versions actually
compiled on 2026-10-02, including ArduinoJson **7.4.3**. See
`compile-only-evidence.json` for package versions, source hashes, binary hash,
RAM/flash usage and known warnings. Both the original resolved configuration
and pinned snapshot compiled successfully. This proves compilation for those
sources and dependencies, not readiness to flash or operate the robot.

## Native handlers, framing and watchdog

With a host C++17 compiler and ArduinoJson headers from that build:

```sh
python robot_dog/firmware/robot-dog-control/test/test_policy_commands.py \
  --json-include /tmp/robot-firmware-build/.pio/libdeps/esp32dev/ArduinoJson/src
```

The script extracts the actual policy-monitor/frame handlers, watchdog, and
serial-line framing from `src/main.cpp`; servo, clock, UART and output
interfaces are fake. It checks read-only monitor gating, valid target handling,
replayed/out-of-range/missing/non-finite targets, monitor no-write behavior,
120 ms command timeout and clock wrap, fragmented lines, and oversized lines
with valid-looking tails. Firmware command dispatch beyond the extracted
handlers and actual UART timing/electrical behavior are not tested here.

This native harness passed with both minimum-declared ArduinoJson **7.0.4** and
actual ESP32-build ArduinoJson **7.4.3**, using g++ **14.2.0**. The older harness
was repaired to include the current target-count diagnostic helper; production
firmware behavior, baud settings, watchdog constants and motor defaults were
not changed.
