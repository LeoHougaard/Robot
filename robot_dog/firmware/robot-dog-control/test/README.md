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

The production `platformio.ini` still uses floating dependency ranges; its
version label now identifies the unflashed `0.1.18-transportguard` candidate.
The separate `platformio.compile-only.ini` pins the prior tested dependency
snapshot, including ArduinoJson **7.4.3**.

The **current candidate ESP32 build is BLOCKED**, as recorded in
`compile-only-evidence.json`. The pinned Arduino ESP32 framework download
failed checksum verification, a registry mirror returned proxy HTTP 403, and
both the initial attempt and one authorized retry ended with the approval
review canceled. No unchecked package was used. Run the command above in an
authorized environment with working official registry access to finish this
check; do not substitute unverified downloads.

The historical 2026-10-02 build evidence is preserved separately in
`compile-only-evidence-2026-10-02.json`. Its earlier source hashes and binary
are **not** a successful build of this changed candidate.

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
actual ESP32-build ArduinoJson **7.4.3**, using g++ **14.2.0**. The earlier harness repair added the target-count diagnostic helper. Current
transport changes and their deployment limits are described in
[TRANSPORT-GUARD.md](TRANSPORT-GUARD.md); timing constants and motor defaults
remain unchanged.

## Adversarial production-dispatch replay

```sh
python robot_dog/firmware/robot-dog-control/test/test_transport_replay.py \
  --json-include /tmp/robot-firmware-build/.pio/libdeps/esp32dev/ArduinoJson/src \
  --result-json /tmp/firmware-transport-replay.json
```

This adds the real command parser/dispatcher, explicit-arm checks, boot
torque-off, feedback-loss disarm, and torque command paths to the fake-I/O
coverage. Fourteen named scenario groups cover every split point, bytewise
fragments, coalesced lines, each possible 128-byte deletion window, truncated
lines, stale/reordered/replayed sequences, sequence and clock wrap, exactly
120 ms versus 121 ms, trailing bytes/NUL, oversized lines, reconnect abort,
old complete buffered arm/torque-on commands, bounded nonce parsing, lost
CAN/truncated or malformed nonce, and stop/disarm/torque-off batches.
`--case NAME` and `--source PATH` support isolated regression reproduction.
The JSON result records source hashes, named results, zero skipped tests, and
`physical_execution: false`. It counts scenario groups, not loop iterations.

For AddressSanitizer and UndefinedBehaviorSanitizer, add `--sanitize`. In a
ptrace-based sandbox, LeakSanitizer cannot run: use
`ASAN_OPTIONS=detect_leaks=0` explicitly and report that leak checking was not
performed. Never turn a sanitizer runtime error into a passing result.
