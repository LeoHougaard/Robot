# Sim-to-real diagnosis and test order

This is the current evidence-based fault list for the `assembly-1-12dof` policy.
It separates confirmed deployment mismatches from measurements that still need
to be made on the physical robot.

## Fixed in the runtime

1. **Policy action rate did not match training.** The training environment clips
   every action to `previous_applied_action +/- 0.30` at 50 Hz. The old runtime
   sent the actor output immediately. An offline neutral observation produces
   several saturated `+/-1` outputs, so this omission could create a first-frame
   joint jump of 14.3 degrees instead of the trained maximum of 4.3 degrees.
   `run_policy.py` now applies the exact training limit and feeds the applied,
   not requested, action back into observation history.

2. **Drive commands did not match training.** Training low-pass filters forward,
   lateral, and yaw targets with a 0.4 second time constant. The runtime now
   uses the identical 50 Hz update (`alpha = 0.02 / 0.4 = 0.05`).

3. **Raw acceleration was treated as gravity.** Isaac Lab supplies projected
   gravity from rigid-body attitude, not a normalized raw accelerometer vector.
   During leg motion the raw vector includes chassis acceleration. The runtime
   now propagates gravity with the gyro and uses accelerometer direction as a
   magnitude-weighted drift correction.

4. **The result was hard to inspect.** Live telemetry now exposes requested,
   post-limit applied, and encoder-measured policy-space joints. The UI renders
   applied versus measured skeletons, and each remote-control session records a
   downloadable JSONL file for frame-by-frame analysis.

These changes reproduce trained behavior; they do not alter the policy weights.

## Highest-value physical checks

Run these in order. Change one variable at a time and save the session log.

1. **Confirm all 12 semantic mappings after the cable work.** A cable's order on
   the TTL bus does not matter, but a servo ID mounted at the wrong physical
   joint does. Verify FR=`7,8,9`, FL=`1,2,3`, BR=`4,5,6`, BL=`10,11,12`, and verify
   hip-abduction, femur, and knee direction on every leg. The software rejects a
   different map, so the physical IDs must agree with it.

2. **Confirm IMU axes, including sign.** The checked-in calibration currently
   contains a **-90 degree yaw** transform, not +90 degrees. With the body level,
   projected gravity should be close to `[0, 0, -1]`. Physically pitch the nose
   up in the robot's trained forward direction; the 3D body and the projected
   gravity plot must move in the matching direction. Yaw alone cannot correct a
   board mounted upside down or on its side; that needs a full 3D mount matrix.

3. **Compare applied and measured skeletons while supported.** At zero drive,
   the orange measured legs should track the cyan applied legs without a leg
   permutation, opposite movement, large lag, or a persistent offset. Use the
   error readout and downloaded log to identify the exact joint.

4. **Measure the knee linkage rather than assuming it.** The runtime currently
   models each knee drive as `femur + relative_knee` with a 1.0 parent ratio.
   The earlier femur-mixing test proves coupled direction, but not linearity.
   Measure the physical knee angle at several femur and knee commands. If the
   linkage is not a true parallelogram, replace the constant ratio with a fitted
   linkage curve or geometric model.

5. **Measure actuator tracking and power under load.** The training actuator is
   modeled at 1.37 Nm, 8 rad/s, stiffness 22, and damping 0.8. ST3215 register
   speed/acceleration and torque-limit values are not those physical units. Plot
   target versus feedback and record the DC rail during a supported policy run.
   A 7.4 V ST3215 is specified at 19.5 kg-cm, 0.192 s/60 degrees no-load, and up
   to 2.5 A per stalled servo; twelve simultaneous legs can expose supply sag,
   current limiting, or actuator lag that simulation did not model.

6. **Recheck physical neutral against simulation zero.** Each saved servo center
   must place the corresponding simulated joint at its asset default angle. A
   visually symmetric `><` pose is useful but is not sufficient if the USD's
   joint-zero geometry differs. Compare the two skeletons at zero applied action.

7. **Only then assess ground contact.** If supported tracking is correct but the
   robot fails on the floor, compare mass/center of mass, foot friction, backlash,
   frame flex, and contact geometry with the trained domain. Use an external
   camera/pose estimate for actual world speed: the onboard IMU and joint encoders
   cannot recover drift-free x/y motion.

## What the live comparison means

- **Requested** is the actor's raw desired joint residual.
- **Applied** is the post-training-limit joint target translated through servo
  IDs, direction, centers, and the four-bar mapping.
- **Measured** is servo feedback inverted through the same mapping.
- The two 3D skeletons compare applied and measured policy-space geometry. They
  are a diagnostic kinematic model, not a second Isaac physics simulation.
- Body tilt comes from the onboard IMU. World position and true walking velocity
  require an external fresh pose source.
