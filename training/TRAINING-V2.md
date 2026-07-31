# Simple Dog Training V2

V2 deliberately solves one problem at a time. Its first policy learns
sustained, steerable locomotion and mild stumble recovery. A deterministic
path follower handles global navigation; rough terrain and self-righting are
later stages with separate acceptance gates.

## What V2 keeps and fixes

Keep from the successful flat run: the calibrated standing pose, existing PD
drives and limits, physical-front mapping, diagonal trot coordination, and
fixed-rollout checkpoint acceptance.

Fix from the unsuccessful branches: no simulator-only actor inputs, no reward
for standing while a movement command is active, no simultaneous rough-terrain
and get-up problem, no large tilt/push distribution before basic steering
passes, and no policy promotion based only on training reward.

## Core policy

- Input: four frames of gyro, projected gravity, body-frame command, joint
  position/velocity, and previous action.
- Excluded from the actor: simulator-truth base velocity, absolute position,
  absolute heading, terrain heights, and contacts.
- Output: eight joint-position residuals tracked by the existing PD drives.
- Command: forward speed and smoothly changing yaw rate. This describes the
  tangent of a curved local path. A later path follower converts a requested
  world `x, y, yaw` goal into these local commands.

The gyro and projected-gravity inputs represent the main-body IMU. Projected
gravity is not raw accelerometer data: hardware must derive it from an
attitude/gravity estimate. The IMU must be rigidly mounted, with axes and
calibration matched to simulation. At the same 50 Hz policy rate, deployment
also supplies encoder positions, filtered/derived joint velocities, command,
and previous action. The actor needs no absolute yaw or measured base velocity.
IMU state was already present in V1 simulation, so its absence did not cause
the rough-simulation failure; a real robot without it would have a serious
deployment mismatch.

## Reward

V2 has one primary locomotion score:

1. Signed forward progress, saturated at the requested speed.
2. Forward/lateral velocity-tracking quality centered so standing earns zero
   while a movement command is active.

The two values are averaged. Everything else is secondary:

- progress-gated yaw-rate tracking;
- a small, progress-gated diagonal-pair timing reward;
- base stability;
- action-rate, foot-slip, and non-foot-contact penalties;
- a one-time fall penalty.

The diagonal reward retains the useful Boston Dynamics Spot-style trot prior
from V1: front-right is synchronized with back-left, and front-left with
back-right. V2 averages the timing similarities instead of multiplying them,
so the signal remains dense. It is gated by actual progress and cannot pay a
stationary robot. Isaac Lab's public Spot task uses this diagonal-pair
structure; this does not mean Boston Dynamics has disclosed Spot's exact
production reward.

## Stages

### 1. Core

- Flat terrain.
- 512 environments.
- Smooth 0.15-0.30 m/s forward and +/-0.40 rad/s yaw commands.
- 90% resets within +/-5 degrees roll/pitch.
- 10% resets within +/-10 degrees.
- After a 6-10 second timer, a 20% chance that the episode receives a small
  +/-0.10 m/s disturbance.

Pass a deterministic suite containing straight, left-curve, right-curve, and
command-change rollouts. Require survival, at least 70% of commanded progress,
bounded yaw error and lateral drift, and swing/landing evidence from every
foot across multiple seeds.

### 2. Robust

Continue the Core checkpoint with the same observation and reward:

- 80% resets within +/-10 degrees.
- 20% resets within +/-20 degrees.
- After a 6-10 second timer, a 35% chance of up to +/-0.30 m/s disturbances.
- The same command range as Core, with stronger sensor noise.

The Core suite must not regress. A deterministic stress suite adds fixed
pushes and measures recovery time.

### 3. Goal completion

Continue Robust with the same policy and reward. Add a small mix of low-speed,
stop, and turn-in-place commands. This is what lets the deterministic path
follower taper speed near `x, y` and set the final yaw; Core alone is only a
moving-path follower. Standing reward is available only when the command asks
for it, while diagonal timing remains gated off.

### 4. Rough locomotion

Only after Goal completion passes:

- begin with a separate low-complexity mild terrain generator;
- add friction, mass/CoM, actuator-strength, latency, and sensor
  randomization gradually;
- preserve policy and observation normalization across bounded processes;
- preserve or reset optimizer/value state according to the proven GB10
  bounded-continuation mechanism, rather than assuming Adam is always safe;
- evaluate multiple held-out terrain seeds and retain flat regression tests.

### 5. Recovery

Train separately from a whitelist of mechanically reachable side/crouched
poses. A deterministic state machine selects recovery when height/gravity
indicate a fall, then returns to locomotion after a stable upright hold.

## Inference boundary

Deterministic code owns training, promotion, retries, and checkpoint rollback.
Local Qwen is financially free and may review aggregated evaluation batches;
batching still avoids GPU container-switch and wall-time overhead. Codex is
removed from routine cycles and reserved for novel repeated
source/controller failures.

## Commands

```powershell
.\Start-SimpleDogTraining.ps1 -Terrain V2Core -NumEnvs 512 -MaxIterations 500
.\Start-SimpleDogTraining.ps1 -Terrain V2Robust -NumEnvs 512 -MaxIterations 1000 `
  -Checkpoint "<V2 checkpoint>"
.\Start-SimpleDogTraining.ps1 -Terrain V2Goal -NumEnvs 512 -MaxIterations 1250 `
  -Checkpoint "<passing V2 Robust checkpoint>"
```

`MaxIterations` is the total PPO epoch target, so each continuation target
must be higher than its source checkpoint epoch.

The existing status and stop commands are unchanged. V1 tasks and checkpoints
remain separate.
