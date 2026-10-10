# Timing-feature and reference-feasibility diagnostics

These changes do not modify the physical runtime, firmware, saved reference
fits, action limits, policy weights, or historical experiment acceptance gates.
They do not establish deployment readiness.

## Timing: historical behavior and explicit opt-in

V20 Delivery overrides V4's observation builder. V21 Stride/StrideCommand and
V22 use that Delivery method, so they did not execute V4's timing-history
writer. The original Delivery method explicitly retained a fixed timestamp:
its sensor samples, finite differences, gravity filter, and physics advance at
20 ms. The inherited Robust/Sustained/Rough `timing_interval_ms = (18, 39)`
setting therefore did **not** exercise variable actor timing features. Prior
labels describing that path as `18..39 ms sensor age` overstated coverage.

Historical Delivery/Stride presets keep the fixed feature by default through
`timing_feature_perturbation_enabled = False`. Merely applying this patch does
not switch existing training to fictitious sample intervals. The shared writer
appends exactly once per observation and fills reset environments' history
with their first new sample. V4 continues its original sampling behavior,
including random-number consumption. Fixed Delivery observations draw no new
random numbers.

For a deliberately separate **feature-sensitivity experiment**,
`evaluate_delivery_stride.py --timing-assessment` explicitly enables the
18..39 ms feature perturbation, with its existing 0..1-frame causal action-delay
setting. A training experiment can opt in through the same named config field,
but must identify the changed observation distribution and must not treat it
as a matched historical continuation. It is off in all saved presets.

With opt-in, only the actor timing slot is perturbed independently. Sensor
values and their derivatives/filters still use the actual fixed step. This is
not a stale-sensor model, a measured trace replay, or variable action-hold
physics. Those require a separate causal design and matched simulation tests.

Evaluation now records:

- The enabled flag separately from inherited configured interval bounds
- Minimum/maximum normalized timing values actually consumed in the actor's
  six selected history slots, and the count checked
- The active timing model and fixed sensor interval
- Explicit `variable_sensor_sampling = false` and
  `variable_action_hold_physics = false` coverage fields

A requested variable-feature experiment fails instead of returning completed
evidence if observed timing inputs are constant, missing, nonfinite, or outside
the selected bounds. This is a coverage-integrity check, not a locomotion
acceptance threshold. Existing evidence files are not relabeled or rewritten.

## Highlift: raw targets and remaining correction room

Run the CPU diagnostic without Isaac:

```bash
python training/probe_delivery_gait.py > /tmp/gait-reference-diagnostics.json
```

It reads the actual 14-entry `CadStrideSpeedCfg` menu and the delivery profile's
limits, evaluates 201 steady-state samples per command at 20 ms, and reports:

- Unclipped and clipped target peaks and finite-difference target rates
- Per-joint and per-command clipping exposure
- Signed limit margins and positive/negative/bilateral residual headroom
- Whether the entire bounded residual envelope fits
- Whether any allowable residual can recover an over-limit reference

The existing test's hand-written list contained 13 commands and substituted
mixed commands for the actual menu. The new probe binds itself to the source
menu without importing Isaac. Synthetic over-limit fixtures ensure diagnostics
cannot hide clipping by reporting only post-clamp values.

For the checked-in highlift fit/menu, zero-residual targets fit within existing
limits: the raw peak is approximately 0.28690 rad. Minimum bilateral correction
room is approximately 0.01310 rad, smaller than the 0.045 rad maximum residual
correction. About 3.41% of sampled joint/phase entries cannot accept the full
residual range without clipping. This does not mean that those entries always
clip: an actual policy might choose smaller or inward corrections.

These are local linear-reference diagnostics. They omit the filter, slew limit,
servo trajectory, contacts, and load response; target height is not measured
foot clearance. No limits have been enlarged to make the probe pass.

## Verification boundary

```bash
python -m pytest -q training/test_delivery_timing.py training/test_delivery_gait_highlift.py
```

Timing tests execute the actual production observation/reset methods and their
superclass chain with CPU tensor sensor fixtures. They cover default parity,
explicit stress, single history append, selected actor slots, partial/full
resets, V4 random-number parity, and coverage rejection. They do not initialize
Isaac, validate its real lifecycle, run GPU physics, train an actor, or operate
a robot. Matched Isaac evaluation and causal action-hold replay remain unrun.
