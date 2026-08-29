# Physical run data

Pixel Robot records one self-describing JSONL file for every stand/run session.
The format is intentionally line-oriented: Python, Julia, MATLAB, DuckDB, and
stream-processing tools can consume it without Android-specific code.

## Capture lifecycle

- Recording starts before `STAND`, or before a policy run if stand is skipped.
- Starting the policy after stand adds a `phase_start` record to the same file.
- `STOP + TORQUE OFF`, a runtime failure, or service shutdown writes
  `session_end` and finalizes the `.jsonl` file.
- Data is flushed at least every 25 records and at every important event.
- If Android is killed between flushes, the next session renames the surviving
  `.jsonl.partial` file to `.interrupted.jsonl`. Such a file may lack its final
  records and `session_end`, but all complete lines remain valid.
- A session is capped at 512 MiB. Recording is best-effort. If storage or JSON
  serialization fails, the recorder closes the partial file and reports the
  error, but it never blocks robot commands or feedback.

Use **Share last run data** in the Android app. With wireless ADB forwarding,
the browser page also has **Download last run**, backed by
`GET /api/session/latest`. Files remain in app-private storage until shared or
downloaded.

Validate and summarize an exported run with no extra Python packages:

```powershell
python tools\inspect_run_data.py "<robot-run.jsonl>"
python tools\inspect_run_data.py "<robot-run.jsonl>" --json
```

Fit controller timing, per-servo tracking, effective lag, IMU statistics, and
action saturation from one run or a directory of runs. The report also includes
per-servo current coverage and current distributions:

```powershell
python tools\fit_sim_from_run_data.py "<robot-run.jsonl-or-directory>"
```

Create SVG graphs without extra Python packages. Choose the servo ID to inspect:

```powershell
python tools\plot_run_data.py "<robot-run.jsonl>" --servo-id 2
```

This writes all-servo current, selected-servo current, selected-servo
target-versus-measured position, and control-timing graphs beside the run.

The fit reports what the controller can observe and includes explicit warnings
for quantities that the recording cannot identify.

## Envelope

Every line is one JSON object:

```json
{
  "type": "derived_policy_frame",
  "session_id": "uuid",
  "record_index": 123,
  "host_unix_ms": 1787654321000,
  "host_monotonic_ns": 456789012345,
  "data": {}
}
```

`schema_version` is currently `1` and appears in the first `session_start`
record. `host_unix_ms` relates the run to wall-clock events. Use
`host_monotonic_ns`, firmware `sample_ms`, and command sequence numbers for
timing analysis because they are not affected by clock corrections.

## Record types

| Type | Contents |
|---|---|
| `session_start` | Schema version, starting phase, app/Android/USB/firmware identity, complete policy metadata and export manifest, effective servo/IMU calibration, and calibration source. |
| `phase_start` | Transition from stand to policy within one continuous capture. |
| `event` | Motion requests, stand trajectory, preflight positions, measured session gyro bias, Android system samples, disarm reason, and other lifecycle markers. |
| `robot_tx` | Exact parsed JSON command sent to the ESP32, with text fallback for a non-JSON payload. |
| `robot_rx` | Exact parsed JSON message received from the ESP32. |
| `derived_policy_frame` | The exact inference input, action transformations, actuator target, matched feedback, timing, and tracking error for one policy frame. |
| `session_end` | Clean outcome and human-readable termination detail. Absence means the capture was interrupted. |

## Measurements at policy rate

Firmware 0.1.12 uses the proven four-byte synchronized ST3215 feedback read once
per frame and reports the logical angle and status for every configured servo.
It then performs a separate two-byte current read for every servo. The response
contains aligned `ids`, `angles_deg`, `current_raw`, and `status_errors` arrays,
plus independent `feedback_complete` and `current_complete` validity flags.
One current step is 6.5 mA. Missing current remains JSON `null` and cannot stop
the policy.

The compact policy `policy_state` also includes sequence, IMU sample time,
feedback/current/frame processing times, accelerometer in mg, and gyroscope in
degrees per second. Selected-servo idle telemetry retains native position and
speed, drive load, voltage, temperature, motion flags, servo and packet status,
current, and the display-only torque estimate. Raw `robot_rx` records save that
full response. The independent battery monitor classifies the 2S pack as
`normal`, `warning` at <=7.0 V, or `critical` at <=6.6 V. This classification
is warning-only and never gates motion. Voltage sampling pauses during stand
playback and policy control to avoid disturbing servo-bus timing, so those
frames retain and identify the last idle reading. A fresh sample is taken during
policy preflight before the control loop is armed. Raw messages are retained,
so later parsers do not depend on what the current app happens to display.

Each `derived_policy_frame` additionally records:

- input and feedback sequence numbers, sample interval, scheduled tick, frame
  compute time, and ONNX inference time;
- target and smoothed commands;
- body-frame angular velocity and projected gravity;
- 12 joint positions, 12 estimated joint velocities, previous action, and the
  exact 180-value actor observation;
- raw actor output, filtered action, safety-limited applied action, policy-space
  position target, and calibrated servo-degree target;
- maximum target/feedback error and the worst servo ID;
- the complete input `policy_state` used for that inference.

Once per second, an `android_system_sample` event records Pixel thermal status,
phone battery level/voltage/temperature/power source, JVM heap use, free
storage, and the current servo-battery safety classification. The stand
trajectory event contains its measured start, final target,
easing fractions, timing, and maximum per-step motion.

Pixel app 0.2.6 uploads stand trajectories as individually acknowledged
`program_step` records followed by one `program_start`. Each USB JSON line is
under 512 bytes, and firmware does not move until every step has arrived. This
replaces the single 2.4-4.8 KiB `play` line that could be corrupted while the
firmware was busy polling servos.

## Interpretation limits

This captures everything currently observable by the controller, not every
physical quantity. Firmware 0.1.12 records available current samples for all 12
servos at the policy rate using a separate, non-fatal synchronized read. The
larger load/voltage/temperature/status block remains an idle selected-servo
measurement because putting it in the critical policy transaction made feedback
unreliable. There is no foot-contact sensor, force plate, external body pose,
ground-reaction force, or video in this log. Contact and world motion must
therefore be inferred or measured with additional hardware if an identification
task requires them.

For simulation matching, use the complete fit report: per-servo current bias,
range and dropout; measured-versus-commanded joint motion; frame delay and
jitter; selected-servo load, voltage, temperature and status; IMU behavior;
and action saturation. Keep every value's validity flag. Do not replace a
missing sample with zero or treat the UI torque estimate as calibrated force.
