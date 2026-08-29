# Current-aware policy observation contract

The deployed V2 actor remains a four-frame, 180-value policy. Servo current is
recorded alongside that actor but is not inserted into its input: changing the
input shape would make the promoted checkpoint and its normalization invalid.

## Runtime measurement

Firmware 0.1.13 publishes one `current_raw` entry for each ID in `ids` on every
firmware-clocked 20 ms feedback tick. Commands and feedback are pipelined: each
state carries its tick and latest applied command sequence, so Android matches
measurements to the correct action without a lock-step USB round trip. One
register step is 6.5 mA. The current transaction follows the
safety-critical position/speed transaction and has independent validity:

- `feedback_complete` controls the locomotion safety limit;
- `current_complete` describes only the current sample;
- a servo that did not reply has a JSON `null`, never a fabricated zero;
- `feedback_us`, `current_us`, and `frame_us` preserve timing evidence;
- Android records the complete `policy_state` used for inference.

This makes synchronized current available while the existing trained policy is
running. It does not turn the ST3215 into a calibrated foot-force sensor.

## New policy family

A current-aware actor must use a new checkpoint family and metadata contract.
Its proposed per-frame input is:

1. the existing 45 deployable V2 values;
2. 12 actuator-current magnitudes in policy actuator order;
3. 12 current-validity values, where 1 means fresh and 0 means missing/stale.

Four 69-value frames produce a 276-value observation. Current magnitude is used
first because motor-current sign is not yet calibrated through every servo and
four-bar linkage. The rough torque number shown by the UI is for a human; its
no-load deadband must not be used as actor input.

Hardware current normalization must be fitted from recorded runs rather than
assumed from a datasheet. Simulation should start from normalized absolute
actuator effort, then add the measured bias, noise, latency, saturation, and
per-servo dropout distribution. Dropout augmentation must set the validity
value to zero and hold the last finite current value, matching deployment.

## Required simulation fit

Before launching this policy family, export the Pixel training-capture ZIP and
run `pixel_robot/tools/analyze_training_capture.py` on it with
`--require-50hz`. The tool verifies the run, deployed actor, metadata, and
calibration hashes, then writes the fit and graphs. Save `simulation-fit.json`
with the training manifest. The task configuration must consume all identifiable
fields:

- per-servo current bias, range, noise, clipping, coverage, and dropout;
- target-to-measured joint error, lag, and effective speed;
- firmware and Android frame timing, including current-read latency;
- IMU bias/noise and acceleration/gyro distributions;
- requested and applied action saturation;
- selected-servo idle load, speed, voltage, temperature, and status evidence.

Use validity values for missing measurements. Keep quantities that cannot be
identified from the run, such as ground-contact force and isolated motor
inertia, out of the fitted simulator parameters. Recordings, generated graphs,
and fit reports are evidence and remain outside Git.

The exported metadata for this family must declare all of the following:

```json
{
  "schema_version": 2,
  "observation_history": 4,
  "observation_size": 276,
  "observation_frame": [
    "v2_deployable_frame[45]",
    "normalized_actuator_current_magnitude[12]",
    "actuator_current_valid[12]"
  ]
}
```

The Pixel runtime must select the observation builder from this metadata and
refuse a shape mismatch. The current 180-input actor remains on its existing
builder.

## Evidence gate before training

Collect matched standing, unloaded-leg, commanded-motion, and externally loaded
runs. Do not start current-aware training until the physical evidence shows:

- the policy transport sustains the 20 ms frame period;
- current loss never disarms an otherwise healthy policy run;
- critical position feedback remains complete apart from isolated frames
  tolerated by the three-frame firmware limit;
- every servo has enough fresh current coverage to fit its measurement model;
- supply-voltage effects and current clipping are represented in the fit.

Promotion remains deterministic and must include the existing V2 locomotion
suite plus a matched current-dropout stress suite. A current-aware checkpoint
cannot replace a V2 checkpoint merely because training reward improves.

The first firmware 0.1.12 policy run on 2026-08-29 captured 468 complete current
samples for every servo and no incomplete critical-feedback frames. It ran at
26.4 Hz, not 50 Hz, so it proves recording coverage but does not pass the 20 ms
transport gate. Do not use that run to claim a 50 Hz deployment.

Firmware 0.1.13 and Pixel app 0.2.7 implement the clocked pipeline, but the gate
still requires a new operator-run recording whose fit report has
`transport_50hz_gate.passed` set to `true`.

The trimmed 2026-08-29 walking capture
`robot-run-20260829-195709-660-8aea13e2-trimmed-training-capture.zip`
(SHA-256 `bd8d95b3da2eacf578f0d97b328c02e8504d7a1224474dbe5a5e4eaf5d0bd212`)
retains 2,199 policy frames over 50.982 seconds with complete current and
critical feedback for all 12 servos. It is valid evidence for current,
tracking, saturation, and effective-lag fitting. Its measured rate is 43.12 Hz
(23 ms median firmware interval), so it does not close the separate 50 Hz gate.
