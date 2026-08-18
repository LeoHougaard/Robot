# Robot Training operating guide

This repository owns the robot assets, Isaac Lab tasks, training methods,
evaluation, playback, and bounded autoresearch workflow. The NVIDIA GB10 is
the current execution backend; Qwen dashboard/model administration remains in
the separate `GB10` project.

## Current backend

| Item | Value |
|---|---|
| Windows project | `C:\Users\Leo\Code Projects\Robot Training` |
| SSH target | `leo@gx10-ddb2.local` |
| Architecture | ARM64 / `aarch64` |
| Isaac Lab container | `isaac-lab-gb10` |
| Isaac playback container | `isaac-lab-dog-stream` |
| Isaac Sim Onshape container | `isaac-sim-onshape` |
| Remote project root | `/home/leo/isaac-workspace/projects` |
| Training data | `/home/leo/isaac-workspace/projects/training` |
| Autoresearch data | `/home/leo/isaac-workspace/projects/autoresearch` |

## Common commands

Run from PowerShell in this repository.

```powershell
.\Get-IsaacStatus.ps1
.\Start-IsaacLab.ps1
.\Stop-IsaacLab.ps1
```

Train:

```powershell
.\Start-SimpleDogTraining.ps1
.\Start-SimpleDogTraining.ps1 -Terrain Rough -Checkpoint "<V1 checkpoint>"
.\Start-SimpleDogTraining.ps1 -Terrain V2Core -NumEnvs 512 -MaxIterations 500
.\Start-SimpleDogTraining.ps1 -Terrain V2Robust -NumEnvs 512 -MaxIterations 1000 -Checkpoint "<V2 checkpoint>"
.\Start-SimpleDogTraining.ps1 -Terrain V2Goal -NumEnvs 512 -MaxIterations 1250 -Checkpoint "<V2 checkpoint>"
.\Get-SimpleDogTrainingStatus.ps1
.\Stop-SimpleDogTraining.ps1
```

Playback:

```powershell
.\Start-SimpleDogPlayback.ps1 -AllowPrivateLanStreaming
.\Get-SimpleDogPlaybackStatus.ps1
.\Stop-SimpleDogPlayback.ps1
```

Autoresearch:

```powershell
.\Start-RobotAutoresearch.ps1
.\Start-RobotAutoresearch.ps1 -Queue "autoresearch\queues\example.json"
.\Get-RobotAutoresearchStatus.ps1
.\Stop-RobotAutoresearch.ps1
.\Invoke-RobotAutoresearchCodex.ps1
```

Onshape:

```powershell
.\Start-IsaacOnshape.ps1 -AllowPrivateLanStreaming
.\Publish-OnshapeExport.ps1 -ZipPath "<export.zip>" -RobotName "<stable-name>"
.\Test-OnshapeRobot.ps1 -RobotName "<stable-name>"
.\Stop-IsaacOnshape.ps1
```

## Training rules

- Preserve V1 as the known working flat policy.
- Keep V2 checkpoints separate; their 180-value observation differs from V1.
- V2 order is Core, Robust, Goal completion, Rough, then separate recovery.
- Actor observations must be physically deployable: body IMU estimate, joint
  position/velocity, command, and previous action history.
- Deterministic evaluation—not training reward—controls promotion.
- Retain the progress-gated diagonal-pair trot prior.
- Do not mix self-righting resets into the locomotion stages.
- Local Qwen may review aggregated evaluations. Codex is not part of the PPO
  loop and is reserved for novel repeated failures.

See `training/TRAINING-V2.md` for the method and acceptance gates.

## Safety and persistence

1. Confirm remote identity is exactly `leo` before changing backend state.
2. Inspect running workloads before starting or stopping containers.
3. Do not interrupt another workload unless explicitly requested.
4. Never expose SSH keys, passwords, registry tokens, or model credentials.
5. Do not use broad cleanup commands such as `docker system prune`.
6. Do not modify drivers, firmware, Docker daemon settings, accounts, SSH,
   firewall, or system services as an incidental repair.
7. WebRTC playback uses unauthenticated private-LAN ports only after the
   explicit `-AllowPrivateLanStreaming` acknowledgement.
8. Stopping workloads preserves remote projects, assets, logs, and checkpoints.
9. Generated logs, checkpoints, caches, and full rollout records stay out of
   Git; only intentionally curated small demonstrations belong in `artifacts`.

## Source and generated data

The Git repository is authoritative for source. The remote directories are
authoritative for generated checkpoints and run evidence. Deployment scripts
copy explicit source files to the remote project directory and never mount the
Docker socket into an AI-controlled container.

Last reviewed: 2026-07-30.
