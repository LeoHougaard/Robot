# Learned locomotion commissioning

The learned controller runs at 50 Hz and accepts body-frame
`forward_mps`, `lateral_mps`, and `yaw_rate_rps`. The commissioned envelope
is 0-0.18 m/s forward, zero lateral speed, and +/-0.25 rad/s yaw.
`goal_controller.py`
converts a world `x/y/yaw` target into those commands, but it requires a fresh
external pose estimate. The onboard IMU can stabilize heading; it cannot
measure world `x/y` position by itself.

The firmware transport is deliberately locked until all of these are true:

1. IDs 1-12 are unique, enabled, have non-default min/home/max limits, and
   each has a deliberate nonzero torque limit below the servo maximum.
2. Every entry in `assembly-1-12dof.calibration.json` has a measured zero,
   direction, and safe range; both calibration flags are `true`.
3. The robot is lifted or supported, torque is reduced, and the operator sends
   the exact `CALIBRATED_AND_LIFTED` arm confirmation.
4. The exported policy metadata identifies profile SHA `B25A4A05...78035D3`
   and contains a passing deterministic Goal result. The runner rejects the
   historical `615092...` rear-knee profile.

The first trials must remain lifted. The firmware rejects missing targets,
out-of-limit targets, target jumps above 6 degrees per 20 ms frame, stale
sequence numbers, lost feedback, and host pauses over 120 ms. Disarming holds
the last position rather than dropping a loaded leg.

Run the pure goal-controller tests from this directory:

```powershell
python -m unittest -v test_goal_controller.py test_policy_runtime.py
```

After filling the calibration file with measured values and supporting the
robot off the ground, begin with a stationary five-second trial:

```powershell
python run_policy.py --port COM5 --weights policy_weights.npz --metadata policy_metadata.json --calibration assembly-1-12dof.calibration.json --duration 5 --confirm-lifted
```

Then use a small command, still lifted, such as `--forward 0.05` or
`--yaw-rate 0.10`. Do not start floor testing until joint directions, limits,
feedback, IMU axes, watchdog disarming, and lifted motion are all verified.

See `INTEGRATION-HANDOFF.md` for exact provenance, known limits, and the next
thread's integration checklist.
