# Isaac Sim and Isaac Lab on the GB10

This setup deliberately separates the two jobs:

- **Isaac Sim 6.0.1** provides the streamed GUI used to import and prepare an
  Onshape assembly.
- **Isaac Lab 3.0.0-beta2-post1** provides the pinned, headless ARM64 training
  environment with both the stable PhysX backend and the beta Newton/MuJoCo-Warp
  backend.
- The same pinned Lab image runs one streamed, single-environment policy review
  in the separate `isaac-lab-dog-stream` container.
- Both see `/home/leo/isaac-workspace/projects` on the GB10 as
  `/workspace/projects` in their containers.

The official image manifests are pinned by digest. A tiny reproducible Isaac Lab
runtime layer adds only Leo's UID 1001 passwd entry and group-write access to
the generic cache parent; this avoids running the training container as root or
giving host UID 1000 ownership of project files. Containers are unprivileged,
have no restart policy, and do not mount broad host directories or the Docker
socket. Isaac Lab uses Docker bridge networking with no published ports. The
interactive Isaac Sim stream uses host networking because NVIDIA documents that
WebRTC does not work through Docker bridge port publishing.

## One-time setup

Review the
[NVIDIA Omniverse license](https://docs.isaacsim.omniverse.nvidia.com/latest/common/licenses.html),
then explicitly accept it while installing:

```powershell
.\Isaac-GB10.ps1 setup -AcceptNvidiaEula
.\Isaac-GB10.ps1 test-lab
.\Isaac-GB10.ps1 test-newton
```

Setup pulls about 25 GB of compressed ARM64 images. Extracted images and warmed
shader/Python caches need substantially more disk. The helper requires at least
100 GiB free before the first pull.

The first command records acceptance at
`/home/leo/isaac-workspace/.nvidia-eula-accepted`. It does not opt in to NVIDIA
telemetry. `test-lab` runs one iteration of the official Cartpole PhysX/RSL-RL
training path; `test-newton` runs the same bounded check with Newton's
MuJoCo-Warp solver. Both containers remain non-root. Isaac Lab uses Leo's host
UID 1001 with the image's runtime GID 1000, and its named volumes are
initialized for that identity; Isaac Sim retains NVIDIA's UID/GID 1234.

## Newton status

Newton is included in the pinned Isaac Lab image and Isaac Sim 6.0.1. NVIDIA
currently classifies the Isaac Lab integration as beta and the Isaac Sim
integration as experimental. The official stock Newton smoke test is part of
this setup, but the promoted dog policies remain on PhysX until the custom USD,
contact behavior, training task, and deterministic promotion suite pass a
separate Newton migration. This preserves the known-working V1 policy and avoids
silently changing the dynamics behind existing checkpoints.

The reusable Lab container deliberately has no Docker health check. It is an
idle execution shell between jobs, while the official base-image health check
expects a continuously running Isaac application and otherwise reports a false
`unhealthy` state. Training status is checked from the actual trainer process.

## Everyday training commands

```powershell
# Start the reusable headless training container
.\Start-IsaacLab.ps1

# Open a shell at /workspace/projects
.\Isaac-GB10.ps1 shell

# Check both Isaac workloads, Qwen, GPU, image pins, and isolation
.\Get-IsaacStatus.ps1

# Stop training and immediately release GPU/RAM
.\Stop-IsaacLab.ps1
```

Inside the shell, the official current training form is:

```bash
cd /workspace/isaaclab
./isaaclab.sh train --rl_library rsl_rl \
  --task=Isaac-Cartpole-Direct-v0 \
  --num_envs=4096 \
  physics=physx
```

Put custom robot-task packages, checkpoints, and output under
`/workspace/projects`. Do not rely on changes made elsewhere inside the
container; the container is reproducible runtime state, not a backup.

### Simple eight-joint dog training

For an ergonomic front end over the same checked-in launchers and Isaac Lab
configuration, run:

```powershell
.\Start-RobotControlCenter.ps1
```

The loopback-only control center edits a validated JSON profile, shows an
explanation beside every technical setting, starts/stops training, reads live
status and best reward, and retrieves the newest periodic Isaac Lab rollout
video. The selected profile SHA is copied into each run directory, so the UI,
launcher, and Isaac runtime can detect configuration drift. Full usage and the
12-DOF replacement workflow are documented in
[control_center/README.md](control_center/README.md).

The imported `simple-8-joint-dog` has a direct Isaac Lab velocity-tracking task
and RL-Games PPO configuration:

```powershell
# Deploy the local task and start a detached 512-environment, 500-epoch run
.\Start-SimpleDogTraining.ps1

# Continue a compatible policy on curriculum-generated rough terrain
.\Start-SimpleDogTraining.ps1 -Terrain Rough -Checkpoint "<checkpoint>"

# Show the current run, epoch, best reward, outputs, and GPU activity
.\Get-SimpleDogTrainingStatus.ps1

# Interrupt training and stop Isaac Lab to release GPU/RAM
.\Stop-SimpleDogTraining.ps1
```

Use `-NumEnvs` or `-MaxIterations` on the start command for an intentional
override. Starting refuses to replace an active run. The task runs at a 50 Hz
policy rate over 200 Hz PhysX, with eight position-target actions and commands
for forward/lateral velocity and yaw. Observations include base velocity,
projected gravity, commands, joint state, and the preceding action. Rewards
track commanded planar velocity and yaw while penalizing falls, tilt,
unnecessary vertical/angular motion, torque, joint speed, and action changes.

`-Terrain Rough` uses a separate, checkpoint-compatible task and log family.
It retains flat tiles while progressing through robot-scaled random
heightfields and uphill/downhill slopes. All environments begin at curriculum
level zero and advance by traversal distance, so the proven flat gait is not
immediately exposed to the hardest surfaces. Rough outputs are written below
`simple_dog_rough_velocity_direct`; they do not replace the promoted flat
checkpoint.

The task references
`/workspace/projects/training/assets/simple_dog_training.usda`, an authored USD
layer over the generated Onshape asset. The Publisher export did not author an
articulation-root API and exported effectively unbounded joint limits. The
authored layer now adds the articulation root, a calibrated free-standing
joint state, matching drive targets, and a per-joint `+/-40` degree learning
envelope centered on that pose, without modifying the generated base asset.
Those are validated simulation limits, not measured hardware limits; replace
them with authoritative mechanical values in Onshape before treating a learned
policy as hardware-ready.

Isaac Lab's minimal headless app did not compose the Publisher's external glTF
references, which left collision wrappers but no mesh shapes and allowed the
robot to fall through the ground. The start command now checks all nine stable
mesh IDs and, when necessary, uses NVIDIA's Asset Converter to create native
USD geometry below
`/workspace/projects/assets/onshape/simple-8-joint-dog/usd_meshes`. The authored
training layer redirects both visual and collision references to those native
files. A changed Onshape topology is rejected instead of silently training the
wrong asset.

Run launcher state and console output persist under
`/home/leo/isaac-workspace/projects/training/runs/simple_dog`. RL-Games
checkpoints and TensorBoard events persist under
`/home/leo/isaac-workspace/projects/training/logs/rl_games/simple_dog_velocity_direct`.
No ports are published during training. Streaming Isaac Sim, streamed policy
playback, and headless Isaac Lab remain mutually exclusive on this single GPU.

The first 512-environment baseline on 2026-07-29 completed 500 epochs and
8,175,616 frames without a runtime error, but it is intentionally retained as
a diagnostic rather than a usable locomotion policy. Mean velocity error fell
from 0.0411 to 0.0149 while mean episode survival remained only 0.015; the
policy exploited the missing collision geometry and short falling episodes.
Do not continue from that checkpoint.

The repaired rest pose was calibrated over 1,024 parallel environments, then
passed a 12-second perturbation stress test with all 1,024 robots stable. A
separate validation of the final authored USD and task configuration also
recorded zero falls in 614,400 environment-steps, a mean settled base height of
0.15948 m, and mean projected gravity Z of -0.99991. The first fresh 25-epoch
PPO check improved mean episode survival to 0.99833 with zero fall
terminations; its checkpoint and TensorBoard data are retained under the normal
training log path.

The current physical-forward curriculum samples 0.15-0.30 m/s with lateral and
yaw commands disabled. The Onshape assembly's front hips are at body Y=-0.125 m
and its back hips are at Y=+0.125 m, so physical forward is body -Y rather than
the previously assumed body +X. The task uses semantic forward/lateral commands
and a lean reward adapted from Isaac Lab's Spot task: physical body-velocity
and yaw tracking, diagonal gait timing, four-foot air/contact cycling, base
stability, action smoothness, contact-gated slip, and non-foot contact.
Fixed-world heading and displacement remain diagnostics rather than extra
reward rules. Playback requests a fixed 0.25 m/s physical-forward command.
The validated 600-epoch continuation is stored at
`/workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/2026-07-29_22-03-12/nn/simple_dog_velocity_direct.pth`.
Its final mean reward was 57.518, mean velocity error 0.083677 m/s, mean
survival 0.998333, and it recorded zero fall terminations.

A supervised high-speed continuation on 2026-07-29 resumed that policy and
trained the 0.25-0.50 m/s curriculum for approximately 20 minutes across two
segments. Its best diagnostic checkpoint is
`/workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/2026-07-29_23-26-09/nn/simple_dog_velocity_direct.pth`.
At its best epoch it recorded reward 46.119, mean velocity error 0.107261 m/s,
mean survival 0.998333, and zero fall terminations. A later segment through
epoch 2,200 was not promoted because its best reward and velocity error were
worse. Visual review then showed that the task's body +X convention pointed
across the chassis, so this entire policy family was rejected as sideways
shuffling rather than physical-forward walking.

Visual review rejected the first corrected physical-forward checkpoint because
it still used an asymmetric three-leg wiggle. The replacement was researched
against Isaac Lab's Spot gait reward, trained for 900 epochs, and then
reweighted without adding reward terms when playback exposed excessive
four-foot slip. The promoted continuation is
`/workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/2026-07-30_01-26-32/nn/simple_dog_velocity_direct.pth`.
Its final training audit recorded 0.998333 mean survival, 0.086342 m/s mean
velocity error, 0.021289 m/s mean body-lateral speed, and 0.013076 mean heading
error. All four swing-duty fractions were balanced at 0.423-0.444. In live
playback the diagonal contact states alternated, heading alignment remained
above 0.9994, and the robot advanced 2.2826 m with 0.0210 m lateral drift over
12 seconds; contact-phase slip was normally 0.09-0.23.

### Watch the validated policy

```powershell
# Start one robot with a fixed 0.25 m/s command and stream its following camera
.\Start-SimpleDogPlayback.ps1 -AllowPrivateLanStreaming
.\Start-SimpleDogPlayback.ps1 -AllowPrivateLanStreaming -Terrain Rough -Checkpoint "<rough-checkpoint>"
.\Start-SimpleDogPlayback.ps1 -AllowPrivateLanStreaming -Terrain V2Core -Checkpoint "<V2-checkpoint>"
.\Start-SimpleDogPlayback.ps1 -AllowPrivateLanStreaming -Terrain V2Rough -Checkpoint "<V2-checkpoint>"

# Check the exact container and recent motion diagnostics
.\Get-SimpleDogPlaybackStatus.ps1

# Recover a black or wedged WebRTC session and restore RTX Real-Time 2.0
.\Restart-SimpleDogPlayback.ps1 -AllowPrivateLanStreaming
.\Restart-SimpleDogPlayback.ps1 -AllowPrivateLanStreaming -Terrain Rough -Checkpoint "<rough-checkpoint>"

# Release GPU/RAM without removing the policy, logs, or caches
.\Stop-SimpleDogPlayback.ps1
```

Connect the Isaac Sim WebRTC Streaming Client to the private GB10 address
printed by the start command. The `isaac-lab-dog-stream` container is
unprivileged, uses Leo's UID 1001, has `restart=no`, mounts the persistent
project directory and the labeled Isaac Lab caches, and runs the pinned Lab
runtime image. It uses host networking because WebRTC requires it and exposes
TCP 49100 and UDP 47998 to the private LAN without authentication or encryption.
It is mutually exclusive with training, Onshape review, and Qwen.
The playback scene resets `/rtx/rendermode` to `RaytracedLighting`, which is RTX
Real-Time 2.0 in the installed Kit 110 runtime. The explicit restart helper
recreates only the streamed playback container after a black or wedged render
session and preserves every project file and named cache volume.

Earlier acceptance recordings correspond to rejected policies and must not be
used to validate the promoted checkpoint. The live WebRTC stream is the visual
acceptance surface. This remains a simulation learning demonstration, not
hardware-ready control.

For a clean visual starting point, open
`/workspace/projects/scenes/simple_dog_known_good.usda` in streamed Isaac Sim.
It references the training layer rather than modifying the generated Onshape
asset, adds a finite collision ground, gravity, lighting, distinct orange/dark
materials, and places the robot at its 0.24 m test spawn height.

## Onshape Omniverse Publisher to Isaac Lab workflow

The preferred path is the new, free
[Onshape Labs Omniverse Publisher](https://cad.onshape.com/appstore/apps/Onshape%20Labs/69822d641e96df67fb56cea8),
released in July 2026. It exports a ZIP containing `robot.usda` plus referenced
glTF meshes. Unlike the older Isaac Sim-side Onshape importer, Publisher carries
physics-enabled joints, masses, colliders, and drive attributes authored in
Onshape. Stable internal identifiers allow a later export to replace the base
asset while authored Isaac scene layers remain intact.

1. Add the Omniverse Publisher from the Onshape App Store.
2. In the Onshape assembly, open its right-side panel. In **Model preparation**:
   review the mate-derived joints, set physics and drive properties, choose the
   rest position, and mark fixed instances that should be fastened to ground.
   Click **Save**; the annotations are versioned with the assembly.
3. Prefer creating an Onshape version for the exact robot state being trained.
   Select **New Export**, choose **USD (usda + gltf as .zip)**, and download it.
4. Install or update that export on the GB10:

   ```powershell
   .\Publish-OnshapeExport.ps1 `
     -ZipPath "$HOME\Downloads\my-robot.zip" `
     -RobotName my-robot
   ```

   The command validates the archive, installs it at
   `/home/leo/isaac-workspace/projects/assets/onshape/my-robot`, and moves any
   prior export to a timestamped recoverable `.backups` directory. In both
   containers the main asset is
   `/workspace/projects/assets/onshape/my-robot/robot.usda`.

5. Stop Qwen and Isaac Lab if either is running.
6. Download the official
   [Isaac Sim WebRTC Streaming Client 2.0.0 for Windows](https://downloads.isaacsim.nvidia.com/isaacsim-webrtc-streaming-client-2.0.0-windows-x86_64.exe).
   NVIDIA treats downloading or using that client as acceptance of its client
   license.
7. Start the streamed Isaac Sim review session:

   ```powershell
   .\Start-IsaacOnshape.ps1 -AllowPrivateLanStreaming
   .\Isaac-GB10.ps1 logs-onshape
   ```

8. When the logs contain `Isaac Sim Full Streaming App is loaded`, connect the
   WebRTC client to the GB10 address printed by the start command.
9. In Isaac Sim's Content panel, navigate to the robot directory and drag
   `robot.usda` into the stage. Save the composed scene separately below
   `/workspace/projects/scenes`; never author directly into the generated asset
   files.
10. Run the repeatable headless structure and PhysX-load check:

    ```powershell
    .\Test-OnshapeRobot.ps1 -RobotName "my-robot"
    ```

11. Validate collisions, inertias, joint limits, drives, base behavior, and
    articulation stability. SimReady means the metadata is carried across; it
    does not prove that gains, collision approximations, or the task definition
    are physically correct.
12. Stop the streamed app, start Isaac Lab, and create an Isaac Lab asset/task
    configuration that references either the generated `robot.usda` or the
    reviewed layered scene.

For design updates, export a new ZIP and run `Publish-OnshapeExport.ps1` with the
same robot name. If Isaac Sim is open, it should report changed base USD files;
choose **FETCH**. The generated asset updates in place and authored scene layers
reapply through USD composition.

Current `simple-8-joint-dog` validation passes the robot graph and short PhysX
load check: one robot root, eight revolute joints, nine rigid bodies, and nine
collision prims. The generated Publisher base still contains sentinel-sized
limits, while the stronger training layer supplies the calibrated rest state
and simulation-safe limits described above. Review and set intentional
mechanical limits in Onshape before transferring a policy to hardware.

Publisher is explicitly an early-stage Onshape Labs tool. Its current known
limitations include subassemblies, groups, patterns, relations, named rest
positions, reference points, and several mate types (including planar,
cylindrical, pin-slot, ball, parallel, tangent, and width). When a robot depends
on those, use Onshape's native URDF export as the portable fallback (mates map to
joints, inertias are emitted, and meshes may be glTF or STL), then import the
URDF in Isaac Sim and accept that stable associative USD updates are lost.

The stream binds TCP 49100 and UDP 47998 to the GB10 private LAN. NVIDIA states
that these endpoints have no authentication or encryption. The explicit
`-AllowPrivateLanStreaming` switch exists to prevent an agent from opening that
boundary silently. Never expose those ports to the public Internet.

The streamed container also keeps NVIDIA's Hub workstation cache in the
dedicated, labeled Docker volume `isaac-sim6-hub-cache`, mounted at
`/var/cache/hub` and owned by the image's non-root UID/GID 1234. Stopping the
stream preserves this cache; the explicit `cleanup -ConfirmCleanup
-RemoveCaches` command removes it with the other Isaac-owned cache volumes.

## Stop, space, and cleanup

```powershell
# Stop all three exact Isaac containers; preserves all disk state
.\Isaac-GB10.ps1 stop-all

# Report Docker and Isaac project disk use
.\Isaac-GB10.ps1 space

# Remove only reproducible Isaac images; keep caches and projects
.\Isaac-GB10.ps1 cleanup -ConfirmCleanup -RemoveImages

# Remove only Isaac-owned caches/config volumes; keep projects
.\Isaac-GB10.ps1 cleanup -ConfirmCleanup -RemoveCaches

# Remove both images and caches; still keep projects
.\Isaac-GB10.ps1 cleanup -ConfirmCleanup -RemoveImages -RemoveCaches
```

Cleanup resolves only the three exact container names, two pinned base images,
the small local Isaac Lab runtime image, and volumes labeled
`com.leo.workload=isaac-gb10`. It never runs a global Docker prune and always
preserves `/home/leo/isaac-workspace/projects`.

## Primary references

- [Isaac Sim container installation](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/install_container.html)
- [Isaac Sim DGX Spark requirements](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html)
- [Isaac Lab installation and DGX Spark notes](https://isaac-sim.github.io/IsaacLab/develop/source/setup/installation/index.html)
- [Isaac Lab Docker guide](https://isaac-sim.github.io/IsaacLab/develop/source/features/docker_cloud.html)
- [Isaac Lab quickstart and training CLI](https://isaac-sim.github.io/IsaacLab/develop/source/setup/quickstart.html)
- [Isaac Lab Newton backend](https://isaac-sim.github.io/IsaacLab/release/3.0.0-beta2/source/overview/core-concepts/physical-backends/newton/index.html)
- [Isaac Sim Newton backend](https://docs.isaacsim.omniverse.nvidia.com/6.0.1/physics/newton_physics.html)
- [Onshape importer and physics preparation](https://docs.omniverse.nvidia.com/extensions/latest/ext_onshape.html)
- [Onshape Omniverse Publisher documentation](https://onovpub-prod.westus2.cloudapp.azure.com/help/)
- [Onshape native URDF export](https://www.onshape.com/en/blog/cloud-native-cad-software-automatic-updates-new-features)
- [Isaac Sim livestream security and client](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/manual_livestream_clients.html)
