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

A separate 31-command, 1,000-phase neutral-body kinematic screen checked
the reference against the profile's actual joint limits. Individual forward
commands at +/-0.04 m/s, lateral at +/-0.02 m/s and yaw at +/-0.1 rad/s
fit without clipping and leave room for the full bounded residual. Some
lateral-plus-yaw corners already clip 2.53% of requested joint values;
pure lateral 0.04 m/s clips 6.27%, and forward 0.08 m/s clips 8.57%.
Evidence is `training/reviews/20260905-delivery/stride-command-envelope-v1.json`.
This is reference kinematics only, not a walking test. Establish individual
slow axes and stopping before simultaneous command corners. Wider commands
will need a reference that fits the mechanism and a fresh physical rollout;
silently clipping them is not a solution. The original delivery speed and
tracking gates are not reduced by this intermediate acquisition envelope.

Epoch 500 completed and is now the preserved acquisition baseline. The
extended evaluator exactly reproduced epoch 100 before comparing both
actors for 20 and 60 seconds. Over the minute, lateral motion fell from
0.03673 to 0.01477 m/s and tilt from 0.07385 to 0.05126 rad; forward speed
is 0.04610 m/s for 0.04 requested. All eight robots survive and every foot
lands at least 32 times. The inspected video shows steadier travel. This
supports preparing the slow-command stage, while retaining the plane and
the original delivery gates. Rough training is not enabled yet.

## Slow-command stage

`CurrentBodyV21Commands` preserves the plane, reference, residual bound,
physical model and PPO settings. Each 20-second episode begins with four
seconds forward, including startup. Subsequent targets hold for four to six
seconds and select forward/reverse 0.04 m/s, lateral +/-0.02 m/s, yaw
+/-0.1 rad/s or stop. Forward appears twice in the eight-entry menu to retain
the acquired behavior. Posture requests remain neutral throughout actions,
rewards and observations; the inherited independent posture timer is disabled.

Commands smooth once before the observation is built, so the actor and
reference see the same command, matching Pixel's ordering. The evaluator
checks the expected 20 ms command schedule and exponential smoothing against
the actual environment. It also verifies the unchanged acquisition task
against the preserved epoch-500 result before command comparisons.

The old 0.03 m/s progress threshold excludes a 0.02 m/s lateral command.
This stage uses thresholds 0.005 m/s and 0.01 rad/s; credit still caps at
requested speed. All other reward weights and the progress-gated diagonal
prior remain. Seven tests of the actual reward method pass, including slow
tracking beating overspeed, parking and zero-net rocking. Earlier task
defaults remain 0.03 and 0.05, and acquisition metrics reproduce exactly.

The first command preflight compared the zero actor and epoch 500. Both
survived all cases and stopped with every foot down. Epoch 500 tracked slow
forward/reverse but strafed at only +0.0129/-0.0091 m/s for +/-0.02 requested.
Its positive turn achieved the desired yaw while two feet never lifted.
That is a failed moving-foot check. The zero actor and epoch 500 are both
retained as command baselines; forward-only learning is not command mastery.
A second preflight repeats the comparison after explicitly disabling the
inherited posture timer. It completed with exact acquisition retention and
the same main command failures: right strafe reaches only -0.0098 m/s and
positive turning leaves two feet continuously planted. It also measures
about +0.032 rad/s signed yaw drift during forward travel. Both baselines
are preserved under `stride-commands-preflight-v2`; command PPO is the next
step. The inherited current-model mixture is sampled per environment even
with nominal physical dynamics, so compare actors under the same command
task and seed rather than mixing command-stage and acquisition samples.

Planned first continuation: preserve epoch 500 and train to 1,000 total
epochs, seed 42, 128 environments. This continuation has now started as run
`20260906T020330Z-train-6539`. Compare on the same isolated-command
screen, retain a minute-long acquisition check and inspect turn/stride
video. Moving cases must retain every-foot lifting/landing, zero falls,
at least 65% signed commanded speed, the existing tracking/posture limits
and low sideways sway. The follow-up additionally screens six or more
landings per moving foot in the measured 14 seconds, no airborne interval
above one second and no continuously planted moving foot. An unrequested
signed yaw rate above 0.02 rad/s remains a heading failure, including the
known epoch-500 drift. Stop must settle with all feet down. The acquisition
check must retain the epoch-500 forward improvement. Repeat promising cases
with additional seeds before moving to physical-model variation. These are
intermediate gates; original delivery speeds, wider commands and hardware
acceptance remain outstanding.

### Epoch 1000: command gains, rejected for promotion

The original command screen has zero resets and reaches +0.01598/-0.01766
m/s sideways for +/-0.02 requested. Yaw rates reach +0.10879/-0.10284 rad/s
for +/-0.1 requested. Positive turning now lifts every foot, but the negative
turn has only three BL landings in 14 seconds. Forward signed yaw remains
about +0.030 rad/s. Stop is almost motionless and level while FR and BL stay
airborne throughout the measured interval. Those are failed gates, not an
accepted gait. The minute-long forward check retains travel but has more tilt
than epoch 500 (0.06359 versus 0.05126 rad) and slightly more lateral sway
(0.01584 versus 0.01477 m/s). Keep both checkpoints and the earlier baseline.

The next reward revision addresses two directly measured deficiencies. Yaw
error variance changes from 0.09 to 0.01, making the same heading error nine
times more costly. The progress-gated diagonal prior retains its bounded
budget using this same variance, so rocking cannot buy excessive gait credit.
At a stationary command only, each unsupported foot costs 0.25 reward units
per second. Previously, body stillness and posture alone allowed two-foot
balance to score well. This support cost does not affect moving commands or
reward stepping in place. The reference, residual bounds, sensor history,
physical model, command menu, PPO settings and plane remain fixed.

Nine tests of the actual reward method pass, including support comparisons,
stronger yaw tracking, and the existing no-rocking/overspeed checks. A first
test used a ratio of small float32 reward differences and failed due to
rounding; the corrected assertion compares the costs directly at 1e-7
tolerance. Both test logs are retained. Before PPO, require exact acquisition
retention and identical epoch-1000 command motion/contacts with only reward
values changed. The planned bounded continuation is epoch 1000 to 1500;
it has not started. Repeat both standard and stationary-start command screens,
forward endurance and failure-specific video before accepting any improvement.

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

Acquisition and slow commands have separate flat training tasks. The later
terrain factory is implemented; automatic promotion and rough-stage training
are not enabled. No V21 export or Pixel deployment is authorized by a passing
acquisition comparison alone.
