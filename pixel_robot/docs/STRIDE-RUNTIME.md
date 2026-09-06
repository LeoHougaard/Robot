# V21 runtime contract before export

V21 is not supported by the installed app. Keep its current policy and strict
metadata rejection until a complete V21 bundle and matching runtime pass
simulation acceptance and motor-disabled tests. The acquisition comparison
alone cannot authorize policy promotion.

`StrideReference.kt` now implements the isolated reference mathematics and
clock features. It is not connected to policy loading, inference or motors.
Its unit tests compare 157 vectors produced by the actual training module,
including startup, phase boundaries, one minute, varied commands and body
attitudes, zero motion and residual saturation. Combined targets and clock
features agree within 0.00003. Complete observation/action/servo parity and
physical timing remain unverified.

V21 has a 428-value actor: V20's 426 physical observations followed by the
sine and cosine of the reference clock. Its 438-value critic is training
only. The learned outputs are twelve residuals, not twelve motor targets.
The portable bundle must bind the actor, observation normalization, robot
calibration and `training/fits/stride-reference-20260905.json` by hash.
Changing reference geometry, frequency, duty, lift, gains, ramp or residual
scale changes the controller even when the ONNX file stays the same.

For each accepted 20 ms control frame:

1. Build the existing causal sensor history. Previous action means the
   combined, bounded, filtered and slew-limited action acknowledged in the
   feedback sequence. It does not mean the neural residual or the fitted
   internal motor trajectory. The simulation stores its final `_actions`
   in the same history positions.
2. Append the clock for this action frame. The current simulation starts at
   elapsed zero after reset, settles for two seconds and ramps for two more.
   Its reference and actor clock use the same elapsed value. The inherited
   reset path additionally forces the initial stance and zero action/filter
   history for the first second, before the reference ramp begins. Mirror
   that hold explicitly; the current phone loop has no equivalent V21 hold.
   Begin from the reviewed stance, not an arbitrary pose. Reset sensor
   history, action filters and clock together at a new control session.
3. Run the actor, clamp each residual to [-1, 1], multiply by 0.15 and add
   the causal IMU/reference result from `delivery_gait.combine_stride`.
4. Apply the existing per-joint bounds, alpha-0.2 filter and delta-0.2 slew
   limit. Convert to logical joint radians at scale 0.3, then use the
   calibrated coupled-knee mapping to servo degrees. The simulator's fitted
   servo dynamics represent the real servo response; do not apply that fit
   a second time in the phone's target generator.

Use a bounded or integer-based clock for prolonged operation only after
checking it against the training implementation's float32 elapsed counter.
Do not advance gait state with extra catch-up inferences on stale feedback.
Parity must include startup, a phase boundary, multiple cycles, one-minute
operation, session restart, gravity disturbances and actual recorded sensor
frames. Check both combined targets and post-filter servo degrees, in
addition to ONNX output parity.

The variable-command V21 stage now smooths before building the observation,
matching Pixel. Its inherited pre-physics smoothing call is disabled so the
command advances exactly once. The actor and reference see the same command;
the command evaluator checks the expected schedule and smoothing at every
step. Acquisition uses one fixed command, so it does not test transitions.
Complete sensor/command/action parity with the phone remains required.

The initial interface remains forward/lateral velocity plus yaw rate, with
posture requests neutral. These three controls do not replace IMU and joint
feedback. Expanded command support, policy export and the physical 50 Hz USB
test are still separate outstanding work; see [DELIVERY-TEST.md](DELIVERY-TEST.md).
