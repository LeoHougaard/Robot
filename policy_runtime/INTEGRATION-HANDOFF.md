# Assembly 1 policy integration handoff

## Repository ownership

- Hardware/runtime target: `C:\Users\Leo\Code Projects\Robot Dog`
- Isaac Lab/training source: `C:\Users\Leo\Code Projects\Robot Training`
- GB10 checkpoint/evaluation data: `/home/leo/isaac-workspace/projects/training`
- Do not use the old OneDrive project or `Robot Dog - clean clone backup 2026-08-12`.
- Keep training tasks, checkpoints, evaluations, and future exports in Robot
  Training. Only copy a selected portable bundle into this directory.

## Installed bundle

- Profile: `assembly-1-12dof`
- Current raw profile SHA-256:
  `B25A4A05FA5A6439B82B824D2C2C826F2A9CC5AACC274D75EE8B4D39978035D3`
- Canonical JSON profile SHA-256:
  `28e699c154e0bad64867ee715fc38a827ea1881229639fd51cb23636c83850d4`
- Checkpoint SHA-256:
  `235a330a9bca715db303c472bbab84e29989b0f96102efc9e36e5f6bde85f95c`
- Portable weights SHA-256:
  `0152312b60a548bdb6dcbcb92444db2e4278f9e5304c47044aa6491ac6fd13ab`
- Checkpoint:
  `/workspace/projects/training/logs/rl_games/quadruped_v2_assembly_1_12dof/2026-08-12_18-37-21/nn/quadruped_v2_assembly_1_12dof.pth`
- Exporter: `Robot Training\training\export_v2_policy.py`
- Remote export:
  `/workspace/projects/training/exports/assembly-1-12dof-b25a4a05-20260812`

The export passed PyTorch-versus-NumPy actor parity on eight deterministic
random observations. The runtime independently verifies the weights hash,
profile id/SHA, 4x45 observation shape, 12-action shape, 50 Hz rate, 0.25 rad
action scale, passed Goal result, and validated command envelope.

## Evaluation evidence and limits

The selected checkpoint has zero-reset deterministic passes at:

- Core: `.../evaluation/20260812T184347Z-core/result.json`
- Robust: `.../evaluation/20260812T184501Z-robust/result.json`
- Goal: `.../evaluation/20260812T184613Z-goal/result.json`

The deployed commissioning envelope is intentionally limited to:

- forward: 0.00 to 0.18 m/s
- lateral: exactly 0.00 m/s
- yaw rate: -0.25 to +0.25 rad/s

A later stricter stress audit at 0.25 m/s and +/-0.30 rad/s stayed upright
with zero resets but exposed unbalanced right-turn/curve swing duty and only
about 0.136 m/s forward speed on the left curve. That higher envelope is not
promoted. Do not widen the runtime limits until a later checkpoint passes the
new gait-quality audit.

## Runtime observation/action contract

Each policy frame contains, in order:

1. body angular velocity, rad/s (3)
2. projected gravity in the body frame (3)
3. body forward/lateral/yaw command (3)
4. policy-space joint positions, rad (12)
5. `0.05 *` policy-space joint velocities, rad/s (12)
6. previously applied normalized action (12)

Four frames are flattened oldest-to-newest into 180 values. The 12 outputs
are clipped to [-1, 1], limited to the training environment's 0.30 normalized
action change per 20 ms frame, multiplied by 0.25 rad, and mapped through
measured servo zeros/directions. Body commands use the training environment's
0.4 second low-pass filter. Requested, applied, and measured policy-space poses
are exposed in live telemetry and recorded for diagnosis.

The four physical knee servos use the same absolute-lower-link four-bar
mapping as the existing Whole Robot Walk Test UI. For every leg, the runtime
maps `knee servo position = hip-flexion policy position + relative-knee policy
position` before applying the measured servo zero/direction/scale. It applies
the inverse mapping to synchronized feedback before building observations.
The four required `four_bar_follow` entries are explicit in the calibration
template; do not remove them or compensate for the linkage in policy weights.

Policy joint order is FR, FL, BR, BL; each leg is hip abduction, hip flexion,
knee flexion. The servo-id mapping is already recorded in
`assembly-1-12dof.calibration.json`. Do not change it casually.

The enforced physical translation is FR=7/8/9, FL=1/2/3, BR=4/5/6, and
BL=10/11/12. Thus the physical diagonal pairs are FR+BL (7/8/9 + 10/11/12)
and FL+BR (1/2/3 + 4/5/6). `run_policy.py` rejects a calibration with unique
IDs but the wrong semantic permutation. Feedback is matched by servo ID before
being converted into signed simulation-space radians and inverse four-bar knee
angles.

## Physical commissioning state

`assembly-1-12dof.calibration.json` now contains the measured assembly values
and both calibration flags are true. The twelve saved centers came from the
existing Whole Robot Walk Test **Set Neutral** workflow. Alternating leg-side
directions, FR/FL/BR/BL permutation, four-bar knee transforms, IMU body-axis
matrix, gyro bias, and gravity sign are explicit and runtime-validated. The
current joint ranges are conservative lifted-commissioning limits, not measured
mechanical endpoints; do not widen them yet.

Run tests from `policy_runtime`, then test only while the robot is securely
supported/lifted:

```powershell
python -m unittest -v test_goal_controller.py test_policy_runtime.py
python run_policy.py --port COM3 --duration 0.5 --confirm-lifted --torque-off-on-exit
python run_policy.py --port COM3 --forward 0.05 --duration 1 --confirm-lifted --torque-off-on-exit
```

The main browser UI has a learned-policy **Remote Control** panel. It talks only to a
localhost bridge hosted by `run_policy.py`; start it with
`python run_policy.py --ui --port COM3`, then connect the panel to
`http://127.0.0.1:18765`. Disconnect
the UI's direct USB/Wi-Fi board transport before driving. The bridge
preserves the same calibration, command-envelope, serial, feedback, and disarm
gates as direct CLI trials. After **Stand to Neutral**, **Start Remote Control**
opens a session whose forward/yaw sliders update live until **Stop + Disarm**.
A two-second browser-command heartbeat disarms on UI loss. Direct CLI runs still
use bounded durations and require `--confirm-lifted`.

The same runtime can run at boot on a Raspberry Pi 3B connected to the ESP32
over USB, removing the Windows-computer dependency. The Pi installer and
service are in `deploy/raspberry-pi`; they use a stable `/dev/serial/by-id`
device, OS-provided NumPy/pyserial packages, one BLAS thread, automatic service
restart, and `--ui-host 0.0.0.0`. The browser's Runner URL then points to
`http://<pi-hostname>.local:18765`. Keep this unauthenticated control port on a
trusted local network only.

The Remote Control panel has a drive-torque slider. It defaults to 100%
and the browser, Python API, and firmware accept 1-100%. `run_policy.py` applies
the selected limit before arming.

The panel also has **Stand to Neutral** for a robot that is resting or laid
down. The runner reads all 12 encoders, moves toward the saved centers in
increments no larger than 3 degrees, verifies the final pose, and holds it for
the following remote-control session. The policy's conservative joint envelope is unchanged;
shrinking that envelope would prevent recovery from the resting pose. Runner
connection now automatically releases the page's direct board transport.

Dynamic accelerometer vectors are fused with the calibrated gyroscope without the former 700-1300 mg
stationary magnitude gate. Non-finite or near-zero vectors remain invalid.

Remote control requests physical torque-off on Stop + Disarm, heartbeat loss,
page exit, or error. The UI retains live servo/IMU graphs, center trims, leg
mapping, Leg IK, Whole Robot Walk Test, and lower diagnostics for debugging.

The established walk-center workflow is shared with policy calibration:
adjust each leg's Hip/Femur/Knee center, verify it with **Set Neutral** while
supported, connect the local runner, and select **Apply Centers to Learned
Policy**. The UI sends the exact effective centers used by Set Neutral: servo
home plus each Hip/Femur/Knee trim. For an already verified calibration, the
bridge updates all 12 `zero_deg` values by servo ID and shifts each joint's
min/max by the same delta. Direction/scale, four-bar linkage, IMU values, and
both verification flags remain unchanged, so the learned policy and stand
transition immediately share the walk-test neutral pose.

The Remote Control panel can update verified IMU body mounting yaw to any custom
angle from -180 to +180 degrees, with full-circle presets, after the board is
rotated in the body plane. It preserves gyro bias and gravity sign.

## Live lifted commissioning evidence (2026-08-12)

The servos are the 7.4 V / 20 kg variant and the servo rail was corrected to
7.2-7.4 V. With the robot securely supported:

- physical IDs 1-12 answered at 1,000,000 baud with status error `0`;
- synchronized absolute angles and onboard IMU data were present;
- all RAM torque limits read `250` (25%);
- ID 1 and ID 2 direction checks moved about 10 degrees and returned safely;
- Set Neutral reached every saved center within 0.18 degrees;
- a 50-frame exact-target stream sustained 50 Hz with 50/50 contiguous
  feedback, maximum one-frame host lag, and 8.47 ms mean firmware frame time;
- the learned policy completed a 0.5-second stationary trial, a 0.03 m/s
  forward trial for 0.75 seconds, and a 0.05 m/s forward trial for 1.0 second;
- no target-limit, target-step, feedback, status, sequence, finite-value, or
  watchdog guard fired in those completed trials;
- the final OTA firmware and UI filesystem are deployed, the localhost bridge
  is ready on COM3/port 18765, and all 12 physical torque-enable registers were
  verified `0` afterward.

USB policy transport uses 921600 baud, enlarged ESP32 UART buffers, strict JSON
finite-value encoding, and compact feedback. The host no longer aborts on a
short feedback backlog; it uses the newest received state. The independent
firmware stale-command watchdog remains 120 ms. Firmware still samples all 12
servos and the IMU every policy frame.

This is not floor-test approval. Keep the robot supported. Before carrying
load, perform longer lifted forward and yaw trials, visually confirm all four
linkage motions and clearance through their ranges, keep emergency stop access,
and deliberately verify feedback-loss handling. Do not widen the promoted
command envelope or conservative joint limits.

The world-goal layer in `goal_controller.py` needs an external fresh `x/y/yaw`
pose estimate. The onboard IMU alone cannot provide drift-free world position.
Verify emergency disarm, feedback loss, stale-frame watchdog, joint direction,
and safe limits before any loaded floor trial.
