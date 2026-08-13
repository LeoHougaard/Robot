# Robot Control Center

The control center is the ergonomic configuration and monitoring layer for the
repository's Isaac Lab quadruped tasks. It does not duplicate Isaac Sim state:
it saves one JSON control profile that the Windows launcher deploys and the
Isaac Lab task imports at startup.

## Open it

From PowerShell in the repository:

```powershell
.\Start-RobotControlCenter.ps1
```

The server binds only to `127.0.0.1`, stores a random session token in its
ignored local cache, and opens the tokenized URL in the default browser. The
token survives local server restarts so an already-open tab stays authorized.
Closing the UI does not stop a remote training run.

The launcher opens the validated **Assembly 1 quadruped (12 DOF)** profile by
default. The Unitree Go2 reference and blank custom template remain selectable.

## What is editable

Settings are grouped by the part of the workflow they affect:

- **Robot setup:** USD asset, calibrated root pose, semantic forward axis,
  base/foot/undesired contacts, joint order, joint roles, rest positions, drive
  gains, torque and speed limits.
- **Training run:** V2 curriculum stage, environments, PPO cycles, checkpoint,
  seed, and periodic video capture.
- **Surface:** flat, random rough, slopes, or mixed curriculum; height range,
  slope, tile size, friction, and restitution.
- **Motion & timing:** policy rate, action range, forward/lateral/yaw/standing/
  turn commands, hold time, and smoothing.
- **Start & reset:** world start position/orientation, episode duration, fall
  height, randomized tilt/heading, and joint-state perturbations.
- **Domain randomization:** chassis mass scale, center-of-mass offsets, robot
  friction/restitution ranges, and PhysX material buckets.
- **Actuators:** stiffness, damping, effort, velocity, armature, and soft-limit
  margin. Global edits are copied to every joint; the robot table then permits
  per-joint overrides.
- **Robustness:** push timing and strength plus deployable sensor noise.
- **Rewards & safety:** progress, turning, diagonal gait, swing-time, stability,
  action rate, slip, unwanted contact, and fall terms.
- **Advanced:** PhysX solver/contact capacity and the RL-Games PPO optimizer and
  network settings.

Use the global search to find a setting by its label, explanation, JSON path,
or group. Advanced settings stay hidden until **Show advanced** is enabled.
Every technical control has a `?` explanation in the UI.

## Start from known good, then replace the robot

`isaaclab-unitree-go2-12dof.json` reproduces the installed Isaac Lab Unitree
Go2 asset, standing pose, 12 joint names, foot/base mappings, PD gains, motor
limits, action scale, and core simulator defaults. It is the launchable
reference—not a profile derived from this repository's previously trained
eight-joint dog. `custom-12dof-template.json` uses the same required semantic
layout: hip abduction, hip flexion, and knee flexion for all four legs.

The linked NVIDIA Spot workflow is also 12-DOF and explicitly calls out mass,
friction, pushes, x/y/yaw commands, PPO network size, environment count,
iteration count, and video recording. Those are represented as editable
profile settings; the built-in Go2 is used as the replacement baseline because
it is installed in the current Isaac Lab image and exposes ordinary PD motor
parameters that map cleanly to a custom robot.

For the custom robot:

1. Publish its immutable export under
   `/workspace/projects/assets/onshape/` using the existing Onshape workflow.
   The training launcher derives a native-mesh `robot.usd` beside the original
   `robot.usda` without changing the export.
2. Replace the template USD, 12 exact joint names, base link, four foot links,
   and undesired-contact expression.
3. Calibrate the root start pose, every joint rest position, physical forward
   axis, motor limits, and joint directions.
4. Validate articulation, collision geometry, contacts, joint ranges, and the
   standing pose in Isaac Sim; then enable **Robot validated**.
5. Save and start V2 Core. Continue through Robust, Goal, then Rough only after
   the repository's deterministic acceptance gates pass.

Launch validation rejects any joint count other than 12, template placeholders,
wrong semantic maps, unsafe asset paths, invalid rates/ranges, checkpoints from
a different robot profile, and curriculum stage/surface mismatches. Isaac Lab
performs another asset/profile check inside the container before constructing
the scene.

## Monitoring and video

The right rail polls the GB10 for container state, epoch/frame progress, newest
best reward, GPU summary, and the active profile SHA. **Fetch newest** copies
the newest completed training `.mp4` into the ignored local cache and loads it
with range requests so it can seek in the browser.

Enable **Training video** before a run and choose the capture interval and clip
length. Rendering reduces throughput, so periodic short clips are preferable
to continuous recording. Reward is a training signal; recorded behavior and
deterministic evaluation remain the evidence used to promote a checkpoint.

## Configuration identity

The UI displays three distinct states:

- **Unsaved changes** exist only in the browser.
- **Saved profile** is the JSON SHA that the next start will deploy.
- **Isaac runtime** is the profile SHA recorded in the active run directory.

Starting always uses the saved profile. If its SHA differs from the active run,
the UI reports drift; changing a profile never mutates a running Isaac process.
