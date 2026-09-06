# Flat-first stride curriculum

Leo's intent is to avoid making the ground too difficult before walking is
learned. Failure on flat evaluation after mixed-terrain training does not
rule out that problem. The next run starts entirely on a plane. Terrain
difficulty does not increase with elapsed time or training reward alone.

The first command is +0.04 m/s forward. The planned user command interface
is body-frame forward/lateral velocity and yaw rate. Height, roll and pitch
requests remain neutral during acquisition. IMU, joint, current and previous
action history are sensor observations, not additional tasks for the user
to command. There is no measured evidence yet that their dimensionality
caused the previous failure, so removing them is a separate ablation.

## Supporting examples and their limits

[Rudin et al.](https://arxiv.org/abs/2109.11978) demonstrate quadruped learning
with a terrain curriculum and physical deployment. Their
[implementation](https://github.com/leggedrobotics/legged_gym/blob/master/legged_gym/envs/base/legged_robot.py)
moves an environment up a level after sufficient travel and down when
distance falls below half the commanded travel. It also uses three motion
commands alongside body and joint observations. Their
[terrain code](https://github.com/leggedrobotics/legged_gym/blob/master/legged_gym/utils/terrain.py)
increases slope, stair and obstacle heights with difficulty. Some terrain
types retain nonzero difficulty at the first level. This supports progressive
difficulty, not a claim that their exact recipe starts entirely flat.

[Policies Modulating Trajectory Generators](https://arxiv.org/abs/1910.02812)
demonstrates real quadruped locomotion using a policy combined with a periodic
trajectory generator. That supports testing a persistent stride plus learned
feedback. Its robot and trajectory model differ from ours; our controller
still needs measured validation and physical testing.

Our choice of an entirely flat first stage is a conservative application of
these principles to this project's repeated failures. It is not a published
guarantee that this robot will transfer successfully.

## Starting controller and matched comparison

V21 is a separate policy family. V20 checkpoints are incompatible. Its actor
has 428 values: the existing 426 physical observations plus sine/cosine of
the stride controller's clock. Its training-only critic has 438 values.
The 12 network outputs are bounded corrections to a fixed reference, with
normalized scale 0.15, at most 0.045 radians before existing joint bounds,
filtering, slew limits and measured servo dynamics. No true body velocity or
terrain height enters the actor or reference generator.

The reference uses 0.6 Hz diagonal phases, 60% duty, 20 mm swing clearance,
two seconds settling and a two-second ramp. It uses the calibrated sole
Jacobians and causal IMU estimate. It is a local linear model. The zero
network head preserves it exactly. Exploration standard deviation is fixed
at 0.2 and actor learning rate at 0.00015; the old runaway exploration and
adaptive learning rate are not reused.

`stride-acquisition-v1` measured eight replicas at each of four exploration
standard deviations. All 32 completed without resets and landed every foot.
At std 0.2, forward speed was 0.0406-0.0410 m/s, tilt 0.0681-0.0776 rad and
mean absolute lateral speed 0.0365-0.0392 m/s. Zero correction had about
0.0405 m/s forward speed and 0.0384 m/s lateral sway. These are flat,
nominal-model diagnostics, not terrain or hardware acceptance.

The actual V21 environment and preserved zero actor also completed eight
20-second trials under `stride-baseline-v1`. It reproduced about 0.0405 m/s
forward, 0.0383 m/s absolute lateral motion, 0.0784 rad tilt and 0.1803 m
body height. All feet landed and there were no resets. Epoch-zero checkpoint
SHA is `d88391e0a5f95e9e5444018a4be71dc71b2fa260d174ff41af689b4153986741`.

First PPO comparison: seed 42, 128 environments, 100 total epochs. Preserve
this exact zero actor and critic initialization. Compare the learned actor
with it using `evaluate_delivery_stride.py`, the same eight flat replicas,
command and measurement window. Inspect a single-robot movie separately.
Improvement requires lower sway or tilt without losing forward tracking,
ground clearance, foot landings or survival. A higher training reward alone
does not qualify. This comparison cannot promote a delivery policy.

The first 100 epochs completed 819,200 transitions. An exact-source rerun
of the zero actor reproduced the baseline. The learned actor reduced mean
absolute lateral speed from 0.03833 to 0.03640 m/s (5.0%) and tilt from
0.07841 to 0.07000 rad (10.7%). Forward speed changed from 0.04052 to
0.04279 m/s against a 0.04 m/s request. All eight trials completed without
resets and every foot landed. The exact candidate video shows continued
stepping and visible residual sway. This justifies continuing acquisition,
not moving to rough ground or declaring hardware readiness.

Continue the preserved epoch-100 actor, critic and optimizers to 500 total
epochs without changing training conditions. Keep the original 20-second
comparison and add a 60-second check before widening commands. Evaluation
now records five-second windows, absolute yaw rate, minimum height, maximum
tilt and longest continuous airborne interval per foot. The amended
20-second evaluator must first reproduce the epoch-100 aggregate metrics;
the added measurements must not silently change the matched task.

## Terrain progression

1. Acquire and improve the stride entirely on a plane.
2. Establish slow motion commands, turns, transitions and stopping on a
   plane. Verify retained successes and restore sensor/actuator variation
   before claiming robustness.
3. Keep the command distribution fixed while introducing terrain at height
   fractions 0.125, 0.25, 0.5, 0.75 and 1.0. Retain a 50% flat fraction.
   Evaluate the current level on held-out terrain and retain a flat replay
   at every transition. Advance only after directional tracking, posture,
   every-foot landing and no-fall checks pass. If a level regresses, retain
   the earlier checkpoint and continue at the easier level.
4. Full-height held-out terrain and physical-model fidelity checks remain
   required before deployment. Wider terrain and body-pose controls are later
   capabilities. No timer silently enables them.

`delivery_terrain.py` generates normal terrain and scales actual mesh Z and
spawn-origin Z by the same fraction, preserving XY and faces. This avoids
the [Isaac random-uniform generator's ignored difficulty parameter](https://isaac-sim.github.io/IsaacLab/main/source/api/lab/isaaclab.terrains.html)
and avoids rounding a reduced noise step to zero. `verify_delivery_terrain.py`
checks the actual Isaac meshes for all three uneven terrain types at every
nonzero fraction. Zero uses an infinite plane. The first implementation
attempt used an unavailable top-level Isaac function import and failed
before creating the robot; that evidence is preserved and the import was
corrected to the installed height-field module.

Only acquisition has a launchable training task at this point. The later
terrain factory is implemented; automatic promotion and later command-stage
tasks are not enabled. No V21 export or Pixel deployment is authorized by a
passing acquisition comparison alone.
