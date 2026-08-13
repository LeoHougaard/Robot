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
are clipped to [-1, 1], multiplied by 0.25 rad, mapped through measured servo
zeros/directions, limited to 5 degrees per 20 ms host frame, and checked
against calibrated min/max angles.

Policy joint order is FR, FL, BR, BL; each leg is hip abduction, hip flexion,
knee flexion. The servo-id mapping is already recorded in
`assembly-1-12dof.calibration.json`. Do not change it casually.

## Required physical commissioning

`assembly-1-12dof.calibration.json` is deliberately incomplete and has both
calibration flags false. Consequently `run_policy.py` cannot arm yet. Measure
and enter every joint zero, servo-degrees-per-policy-radian direction/scale,
safe min/max, IMU body-axis matrix, gyro bias, and gravity sign. Preserve
robot-specific measurements in that file; never bake them into policy weights.

Run tests from `policy_runtime`, then test only while the robot is securely
supported/lifted:

```powershell
python -m unittest -v test_goal_controller.py test_policy_runtime.py
python run_policy.py --port COM5 --weights policy_weights.npz --metadata policy_metadata.json --calibration assembly-1-12dof.calibration.json --duration 5 --confirm-lifted
python run_policy.py --port COM5 --weights policy_weights.npz --metadata policy_metadata.json --calibration assembly-1-12dof.calibration.json --forward 0.05 --duration 3 --confirm-lifted
python run_policy.py --port COM5 --weights policy_weights.npz --metadata policy_metadata.json --calibration assembly-1-12dof.calibration.json --yaw-rate 0.10 --duration 3 --confirm-lifted
```

The world-goal layer in `goal_controller.py` needs an external fresh `x/y/yaw`
pose estimate. The onboard IMU alone cannot provide drift-free world position.
Verify emergency disarm, feedback loss, stale-frame watchdog, joint direction,
and safe limits before any loaded floor trial.
