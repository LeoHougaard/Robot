# Current-aware rough policy design

Decision date: 2026-08-29

## Evidence boundary

The calibration source is the trimmed capture
`robot-run-20260829-195709-660-8aea13e2-trimmed-training-capture.zip`, SHA-256
`b1389bf17ee37737674b0bb57c477ceb4b517eeb6038bdee88c77536fc799254`.
It contains 2,189 complete policy frames over 50.727 seconds. Every servo has
complete current coverage. The measured policy rate is 43.13 Hz, the median
firmware interval is 23 ms, and the separate 50 Hz transport gate fails. The
capture identifies closed-loop response and observation statistics, not motor
inertia, ground-reaction force, terrain friction, or isolated linkage load.

## Policy contract

Create a new `CurrentV3` family. V1 and V2 task names, observations, checkpoints,
normalization, and exports remain unchanged.

The actor receives the existing four-frame V2 history plus current data:

- 45 deployable V2 values per frame;
- 12 normalized absolute servo-current values in policy actuator order;
- 12 fresh-current validity values.

Four 69-value frames retain the proposed 276-value current-aware history. Append
one current desired posture command, `[height_offset_m, roll_rad, pitch_rad]`,
for 279 actor values. Repeating a controller command in every history frame adds
no measurement history, so the single appended triple is the smallest addition
that meets the posture requirement. The runtime reads the observation layout
from metadata and rejects every other shape.

Absolute current is a load/contact cue, not force. Runtime normalization uses a
per-servo low-activity bias and robust working range fitted from this capture.
Missing or stale current sets validity to zero and holds the last finite
normalized value. This matches the firmware and Pixel data contract. Prior work
has found motor current or joint torque useful for estimating foot load, while
also showing that temporal context is needed to separate contact from actuator
dynamics [Urbain et al., 2021](https://doi.org/10.3389/fnbot.2021.655330).

## Simulation model

Use a measured, bounded position-servo model instead of an actuator neural
network. Per servo, consume fitted command-to-feedback lag, best aligned
residual error, measured speed, target saturation, current bias/range/noise,
observed current clipping, timing jitter, and dropout. Model transport delay as
a short command buffer, then apply a randomized first-order response and the
measured speed limit. Add only the residual aligned error after lag compensation.
This avoids counting the same lag twice.

Isaac Lab supports delayed PD and learned actuator models, and its guidance is
to choose the simplest model that meets the requirement
[Isaac Lab actuator concepts](https://isaac-sim.github.io/IsaacLab/develop/source/concepts/actuators.html).
Hwangbo et al. used an actuator network successfully when they had dedicated
actuator data [Hwangbo et al., 2019](https://arxiv.org/abs/1901.08652). This
capture is closed-loop walking data, so fitting a neural actuator would claim
separation the recording does not provide.

Simulated current starts from absolute applied joint effort, maps it through the
per-servo fitted current range, adds the fitted noise and latency, clips at the
observed finite bound, and applies dropout with value holding. The empirical
dropout rate is zero, but training and the deterministic stress suite include
bounded missing-current bursts so the policy cannot depend on perfect USB
transport.

## Posture and rough terrain

Sample smooth height offset, roll, and pitch commands independently of planar
and yaw commands. Reward commanded body-to-support-foot height and commanded
gravity direction. Do not add base height, terrain samples, contact state,
world position, world heading, or simulator velocity to the actor. PALo reports
simultaneous body height, roll, pitch, and velocity commands with a proprioceptive
quadruped policy [Wang et al., 2025](https://arxiv.org/abs/2503.04462).

Retain the progress-gated diagonal-pair prior and existing safety/contact terms.
Use the existing mild terrain generator and command-aligned curriculum, then
widen only passing continuations. Isaac Lab exposes terrain levels and
`update_env_origins` for game-style curricula
[Isaac Lab terrain API](https://isaac-sim.github.io/IsaacLab/develop/source/api/lab/isaaclab.terrains.html).
Rudin et al. found this curriculum structure effective for massively parallel
rough-terrain locomotion [Rudin et al., 2022](https://proceedings.mlr.press/v164/rudin22a.html).

Randomize mass, inertia-related mass distribution, center of mass, actuator
strength/response, friction, restitution, observation noise, timing, and pushes
within physical bounds. Use roughly 25 percent variation for mass and actuator
quantities where geometry and safety limits allow it. Friction, restitution,
latency, and noise use fitted or literature-backed bounds rather than a blanket
percentage. Isaac Lab provides explicit mass, center-of-mass, material, joint,
gain, force, and push randomizers
[Isaac Lab event API](https://isaac-sim.github.io/IsaacLab/v2.0.1/source/api/lab/isaaclab.envs.mdp.html).
Lee et al. showed that a history of proprioception with randomized training can
transfer across rough natural terrain without terrain truth in the deployed
policy [Lee et al., 2020](https://arxiv.org/abs/2010.11251).

## Promotion gate

Compare every candidate with the retained baseline under the same fixed seeds.
Run flat regression, held-out rough mobility, stand/stop, crouch/height, roll,
pitch, current-dropout bursts, pushes, and broad robot randomization. Require
signed progress, command tracking, survival, safe contacts, all-foot swing and
landing, bounded stance-foot slip, and posture error. Isaac Lab's official
locomotion configurations use contact-conditioned foot-air-time and foot-slide
terms rather than trusting velocity reward alone
[Isaac Lab locomotion configuration](https://github.com/isaac-sim/IsaacLab/blob/release/3.0.0-beta2/source/isaaclab_tasks/isaaclab_tasks/manager_based/locomotion/velocity/config/h1/rough_env_cfg.py).

Render every policy considered for promotion. Numeric success is inconclusive if
the video does not rule out dragging, planted feet, wrong direction, body
contact, or sliding. Training reward never promotes a checkpoint.

## Remaining physical uncertainty

The capture has one recorded battery value and no synchronized force, contact,
external pose, or video. Voltage dependence, motor temperature dependence,
ground force, exact friction, and independent motor/linkage dynamics remain
unidentified. Domain randomization covers them in simulation, but only a future
operator-run 50 Hz capture and staged physical tests can close those gaps.
