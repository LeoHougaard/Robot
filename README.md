# Robot

[![CI](https://github.com/LeoHougaard/Robot/actions/workflows/ci.yml/badge.svg)](https://github.com/LeoHougaard/Robot/actions/workflows/ci.yml)

This monorepo contains the robot's training system, physical controller and
runtime, and Pixel Android controller. The training project remains at the
repository root so its existing launchers keep working.

## Projects

| Path | Purpose |
|---|---|
| Repository root | Isaac Lab training, evaluation, policy export, playback, and autoresearch |
| `robot_dog/` | ESP32 firmware, Raspberry Pi deployment, browser controls, and portable policy runtime |
| `pixel_robot/` | Android USB host, on-device policy runtime, camera, and robot controls |

Each project retains its original Git history. Open this repository when a
change crosses training, export, firmware, runtime, or Android boundaries.

## Offline LLM command research

[`robot_llm/`](robot_llm/README.md) provides a strict intent contract, a
non-actuating shadow supervisor, and an offline-first comparison of JSON-object,
native JSON-Schema, and optional pinned BAML output. It has no robot transport
and cannot arm or move the robot. Fixture replay verifies the harness, not a
language model's accuracy or physical safety. Live inference is explicitly
opt-in and requires an endpoint and model selected by the operator.

The existing training adviser remains in [`autoresearch/`](autoresearch/README.md).
Its typed response validation does not grant it authority over physical motion
or deterministic checkpoint promotion.

After the isolated setup in [`validation/README.md`](validation/README.md), run
`python -m validation` for the canonical offline checks and a machine-readable
pass/fail/blocked report. A bounded watch mode reruns checks after fixes without
editing code itself. CI uses the same runner. Missing live-model, simulator or
hardware evidence cannot become a physical-readiness pass.

Use [`robot_llm/SESSION.md`](robot_llm/SESSION.md) for the interactive shadow
session and [`robot_llm/QUALIFICATION.md`](robot_llm/QUALIFICATION.md) for the
offline gate on retained live-model benchmark evidence. The larger
[`qualification corpus`](robot_llm/QUALIFICATION-CORPUS.md) contains authored
test instructions, not measured model results.

The [current Pixel compute-only bridge](robot_llm/RUNTIME-BRIDGE.md) joins the
shadow command lease to the checked-in 428-input ONNX actor and production
Kotlin observation/stride/calibration math. It uses synthetic feedback and has
no USB, arming, or actuation path. See the
[read-only setup preflight](deployment_readiness/README.md) for dependency and
bundle diagnostics, and [Pixel host tests](pixel_robot/docs/HOST-TESTS.md) for
what can be compiled and tested without an Android device.
The app also includes a [shadow-only text preview](pixel_robot/docs/SHADOW-PREVIEW.md)
with explicit provider consent and service-owned expiry; it never feeds accepted
previews into the physical controller.

## Training system

The reinforcement-learning infrastructure trains quadrupeds in NVIDIA Isaac
Lab. The control-center workflow starts from Isaac Lab's official 12-DOF
Unitree Go2 reference and provides a gated 12-DOF custom-robot profile.

Training currently runs on an NVIDIA GB10/DGX Spark. The repository is
organized around the robot, training tasks, evaluation, and policy promotion.

## Current capabilities

- Imports and validates an Onshape quadruped assembly.
- Trains flat and rough-terrain locomotion with RL-Games PPO.
- Provides pinned PhysX and beta Newton/MuJoCo-Warp Isaac Lab backends while
  retaining PhysX for promoted dog policies.
- Streams trained policies through Isaac Sim WebRTC.
- Preserves checkpoints, metrics, videos, and provenance outside containers.
- Runs bounded autoresearch cycles with deterministic checkpoint promotion.
- Includes a second-generation training method with deployable observations,
  curved-path commands, disturbance recovery, and goal-completion stages.
- Provides a local control-center UI for robot mapping, joint drives, surfaces,
  physics, resets, rewards, PPO, run control, progress, best reward, and rollout
  video.

[Watch the first validated forward-walking policy](artifacts/simple-dog-walking/simple-dog-forward-walk.mp4).

## Training V2

V2 separates concerns so one policy is not asked to learn every behavior at
once:

1. Core flat-ground locomotion and smooth curved-path following.
2. Robustness to modest tilt, pushes, and sensor noise.
3. Full forward/reverse/strafe/turn commands for final `x, y, yaw` completion
   through a deterministic pose controller above the learned policy.
4. Rough-terrain locomotion after the flat regression suite passes.
5. Self-righting as a separately trained and selected recovery skill.

The actor uses only signals intended for the physical robot: body gyro,
estimated gravity direction, joint position/velocity, command, and previous
action history. The reward keeps the successful Spot-style diagonal trot
coordination while making commanded progress the primary objective.

See [training/TRAINING-V2.md](training/TRAINING-V2.md) for the complete method.

## Repository layout

```text
training/       Isaac Lab tasks, PPO configurations, validation, and V2
autoresearch/   deterministic training/evaluation supervisor and manifests
scenes/         authored Isaac scene layers
artifacts/      small curated demonstrations
robot_dog/       physical robot firmware, deployment, UI, and policy runtime
pixel_robot/     Pixel Android controller and on-device policy runtime
*.ps1           Windows launch, status, playback, and deployment commands
isaac-gb10.sh   current GB10 container backend
```

Generated checkpoints, logs, caches, imported Onshape assets, and full rollout
records are intentionally not committed. On the configured machine they remain
under `/home/leo/isaac-workspace/projects`.

## Quick start

The checked-in launcher currently expects:

- Windows PowerShell and OpenSSH;
- NVIDIA Sync access to `leo@gx10-ddb2.local`;
- an ARM64 GB10 with Docker and an NVIDIA GPU;
- NVIDIA Isaac Lab/Isaac Sim container access;
- explicit acceptance of NVIDIA's Omniverse license.

After reviewing the license:

```powershell
.\Isaac-GB10.ps1 setup -AcceptNvidiaEula
.\Isaac-GB10.ps1 test-lab
```

Open the local training control center:

```powershell
.\Start-RobotControlCenter.ps1
```

It starts from the official Isaac Lab Unitree Go2 12-DOF reference profile.
The custom 12-DOF template cannot launch until its USD asset, semantic
joint/contact mappings, and standing pose have been replaced and marked
validated. See
[control_center/README.md](control_center/README.md) for the setting map and
workflow.

Start V2 Core training:

```powershell
.\Start-SimpleDogTraining.ps1 -Terrain V2Core -NumEnvs 512 -MaxIterations 500
```

Inspect or stop it:

```powershell
.\Get-SimpleDogTrainingStatus.ps1
.\Stop-SimpleDogTraining.ps1
```

Deterministic promotion (runs only while training/playback is idle):

```powershell
.\Test-SimpleDogPolicy.ps1 -Stage Core -Checkpoint "<V2 checkpoint>" -ControlProfile ".\control_center\profiles\assembly-1-12dof.json"
```

See [ISAAC-GB10.md](ISAAC-GB10.md) for backend setup, persistence, streaming,
and cleanup details.

## Safety

The launchers use named containers and explicit project directories. Stopping
a workload preserves project data and checkpoints. Do not use broad Docker
cleanup commands, expose WebRTC ports publicly, commit credentials, or mix V1
and V2 checkpoints.

## License

BSD 3-Clause. See [LICENSE](LICENSE) and
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
