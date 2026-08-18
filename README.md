# Robot Training

[![CI](https://github.com/LeoHougaard/Robot-Training/actions/workflows/ci.yml/badge.svg)](https://github.com/LeoHougaard/Robot-Training/actions/workflows/ci.yml)

Reinforcement-learning infrastructure for training quadrupeds in NVIDIA Isaac
Lab. The control-center workflow starts from Isaac Lab's official 12-DOF
Unitree Go2 reference and provides a gated 12-DOF custom-robot profile.

The project currently executes on an NVIDIA GB10/DGX Spark, but the repository
is organized around the robot, training tasks, evaluation, and policy
promotion—not around the computer that runs them.

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
