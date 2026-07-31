"""Validate the authored Simple Dog rest pose, limits, and ground contact."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from isaaclab.app import AppLauncher


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--num-envs", type=int, default=1024)
parser.add_argument("--steps", type=int, default=600)
parser.add_argument(
    "--output-dir",
    type=Path,
    default=Path("/workspace/projects/training/diagnostics"),
)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()

app_launcher = AppLauncher(args)
simulation_app = app_launcher.app

try:
    import gymnasium as gym
    import torch

    import simple_dog_task  # noqa: F401
    from simple_dog_task.simple_dog_env_cfg import SimpleDogFlatEnvCfg

    cfg = SimpleDogFlatEnvCfg()
    cfg.scene.num_envs = args.num_envs
    cfg.episode_length_s = 100.0
    cfg.command_forward = (0.0, 0.0)
    cfg.command_lateral = (0.0, 0.0)
    cfg.command_yaw = (0.0, 0.0)
    cfg.standing_command_fraction = 1.0

    env = gym.make("Isaac-Velocity-Flat-Simple-Dog-Direct-v0", cfg=cfg, render_mode=None)
    base_env = env.unwrapped
    robot = base_env._robot
    observations, _ = env.reset()
    base_env.episode_length_buf[:] = 0

    ever_fell = torch.zeros(base_env.num_envs, dtype=torch.bool, device=base_env.device)
    fall_events = 0
    actions = torch.zeros(
        (base_env.num_envs, robot.num_joints),
        dtype=torch.float,
        device=base_env.device,
    )
    for _ in range(args.steps):
        observations, _, terminated, truncated, _ = env.step(actions)
        del observations, truncated
        ever_fell |= terminated
        fall_events += torch.count_nonzero(terminated).item()

    height = robot.data.root_pos_w.torch[:, 2] - base_env.scene.env_origins[:, 2]
    gravity_z = robot.data.projected_gravity_b.torch[:, 2]
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "num_envs": base_env.num_envs,
        "steps": args.steps,
        "duration_seconds": args.steps * base_env.step_dt,
        "joint_names": list(robot.joint_names),
        "default_joint_pos_radians": robot.data.default_joint_pos.torch[0].tolist(),
        "hard_joint_limits_radians": robot.data.joint_pos_limits.torch[0].tolist(),
        "ever_fell_fraction": ever_fell.float().mean().item(),
        "fall_events": fall_events,
        "final_mean_height": height.mean().item(),
        "final_min_height": height.min().item(),
        "final_mean_gravity_z": gravity_z.mean().item(),
        "final_max_gravity_z": gravity_z.max().item(),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-stability-validation.json"
    )
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"STABILITY_RESULT={output_path}", flush=True)
    print(json.dumps(result, indent=2), flush=True)
    env.close()
finally:
    simulation_app.close()
