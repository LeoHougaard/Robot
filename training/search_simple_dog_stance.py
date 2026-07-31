"""Search for a stable held stance for the imported eight-joint dog.

This is a simulation calibration tool, not policy training. It evaluates many
joint poses in parallel, keeps the best candidates, then stress-tests the best
pose with small perturbations. Results are written below the persistent project
directory for review before changing the task configuration.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

from isaaclab.app import AppLauncher


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--num-envs", type=int, default=1024)
parser.add_argument("--generations", type=int, default=5)
parser.add_argument("--settle-steps", type=int, default=100)
parser.add_argument("--stress-steps", type=int, default=600)
parser.add_argument("--output-dir", type=Path, default=Path("/workspace/projects/training/diagnostics"))
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()

app_launcher = AppLauncher(args)
simulation_app = app_launcher.app

try:
    import gymnasium as gym
    import omni.kit.app
    import torch

    extension_manager = omni.kit.app.get_app().get_extension_manager()
    extension_manager.set_extension_enabled_immediate("omni.kit.asset_converter", True)
    for _ in range(3):
        simulation_app.update()

    import simple_dog_task  # noqa: F401
    from simple_dog_task.simple_dog_env_cfg import SimpleDogFlatEnvCfg

    if args.num_envs < 128:
        raise ValueError("--num-envs must be at least 128")

    cfg = SimpleDogFlatEnvCfg()
    cfg.scene.num_envs = args.num_envs
    cfg.episode_length_s = 100.0
    cfg.action_scale = 1.45
    cfg.command_forward = (0.0, 0.0)
    cfg.command_lateral = (0.0, 0.0)
    cfg.command_yaw = (0.0, 0.0)
    cfg.standing_command_fraction = 1.0
    # Disable task resets during calibration; the scoring function determines
    # whether a candidate fell.
    cfg.termination_height = -10.0
    cfg.termination_projected_gravity_z = 2.0

    env = gym.make(
        "Isaac-Velocity-Flat-Simple-Dog-Direct-v0",
        cfg=cfg,
        render_mode=None,
    )
    base_env = env.unwrapped
    robot = base_env._robot
    device = base_env.device
    num_envs = base_env.num_envs
    num_joints = robot.num_joints
    if num_joints != 8:
        raise RuntimeError(f"Expected 8 joints, got {num_joints}")

    env.reset()
    import omni.usd
    from pxr import Usd, UsdGeom, UsdPhysics

    stage = omni.usd.get_context().get_stage()
    runtime_robot = stage.GetPrimAtPath("/World/envs/env_0/Robot")
    runtime_meshes = [prim for prim in Usd.PrimRange(runtime_robot) if prim.IsA(UsdGeom.Mesh)]
    runtime_collisions = [
        prim for prim in Usd.PrimRange(runtime_robot) if prim.HasAPI(UsdPhysics.CollisionAPI)
    ]
    ground_collisions = [
        prim
        for prim in stage.Traverse()
        if str(prim.GetPath()).startswith("/World/ground")
        and prim.HasAPI(UsdPhysics.CollisionAPI)
    ]
    print(
        "RUNTIME_GEOMETRY "
        f"robot_meshes={len(runtime_meshes)} "
        f"robot_collision_apis={len(runtime_collisions)} "
        f"ground_collision_apis={len(ground_collisions)}",
        flush=True,
    )
    print(
        "GROUND_COLLISION_PATHS="
        + ",".join(str(prim.GetPath()) for prim in ground_collisions),
        flush=True,
    )
    all_ids = torch.arange(num_envs, device=device, dtype=torch.long)
    generator = torch.Generator(device=device)
    generator.manual_seed(20260729)

    search_mean = torch.zeros(num_joints, device=device)
    search_std = torch.full((num_joints,), 0.75, device=device)
    joint_floor = torch.full((num_joints,), -1.40, device=device)
    joint_ceiling = torch.full((num_joints,), 1.40, device=device)
    elite_count = max(32, num_envs // 32)
    generation_records: list[dict] = []

    def place_candidates(candidates: torch.Tensor, root_height: float = 0.30) -> None:
        robot.reset(all_ids)
        base_env.episode_length_buf[:] = 0
        base_env._actions[:] = 0.0
        base_env._previous_actions[:] = 0.0

        root_pose = robot.data.default_root_pose.torch.clone()
        root_pose[:, :3] += base_env.scene.env_origins
        root_pose[:, 2] = base_env.scene.env_origins[:, 2] + root_height
        root_velocity = torch.zeros_like(robot.data.default_root_vel.torch)
        joint_velocity = torch.zeros_like(robot.data.default_joint_vel.torch)

        robot.write_root_pose_to_sim_index(root_pose=root_pose, env_ids=all_ids)
        robot.write_root_velocity_to_sim_index(root_velocity=root_velocity, env_ids=all_ids)
        robot.write_joint_position_to_sim_index(position=candidates, env_ids=all_ids)
        robot.write_joint_velocity_to_sim_index(velocity=joint_velocity, env_ids=all_ids)

    def evaluate(candidates: torch.Tensor, steps: int) -> tuple[torch.Tensor, dict[str, torch.Tensor]]:
        place_candidates(candidates)
        actions = torch.clamp(
            (candidates - robot.data.default_joint_pos.torch) / cfg.action_scale,
            -1.0,
            1.0,
        )
        for _ in range(steps):
            env.step(actions)

        height = robot.data.root_pos_w.torch[:, 2] - base_env.scene.env_origins[:, 2]
        gravity_z = robot.data.projected_gravity_b.torch[:, 2]
        linear_speed = torch.linalg.vector_norm(robot.data.root_lin_vel_w.torch, dim=1)
        angular_speed = torch.linalg.vector_norm(robot.data.root_ang_vel_w.torch, dim=1)
        joint_speed = torch.mean(torch.abs(robot.data.joint_vel.torch), dim=1)
        upright = torch.clamp(-gravity_z, 0.0, 1.0)
        stable_height = torch.exp(-torch.square((height - 0.20) / 0.06))
        low_motion = torch.exp(-(linear_speed + 0.35 * angular_speed + 0.08 * joint_speed))
        survived = (height > 0.13) & (gravity_z < -0.65)
        score = 3.0 * upright + 3.0 * stable_height + 2.0 * low_motion + 5.0 * survived.float()
        return score, {
            "height": height,
            "gravity_z": gravity_z,
            "linear_speed": linear_speed,
            "angular_speed": angular_speed,
            "joint_speed": joint_speed,
            "survived": survived,
        }

    best_pose = None
    best_score = float("-inf")
    for generation in range(args.generations):
        noise = torch.randn(
            (num_envs, num_joints),
            generator=generator,
            device=device,
        )
        candidates = torch.clamp(search_mean + noise * search_std, joint_floor, joint_ceiling)
        score, metrics = evaluate(candidates, args.settle_steps)
        elite_indices = torch.topk(score, elite_count).indices
        elites = candidates[elite_indices]
        search_mean = elites.mean(dim=0)
        search_std = torch.clamp(elites.std(dim=0, unbiased=False) * 0.80, 0.05, 0.70)

        generation_best_index = torch.argmax(score)
        generation_best_score = score[generation_best_index].item()
        if generation_best_score > best_score:
            best_score = generation_best_score
            best_pose = candidates[generation_best_index].clone()

        record = {
            "generation": generation,
            "best_score": generation_best_score,
            "survival_fraction": metrics["survived"].float().mean().item(),
            "elite_mean": search_mean.tolist(),
            "elite_std": search_std.tolist(),
        }
        generation_records.append(record)
        print(json.dumps(record), flush=True)

    if best_pose is None:
        raise RuntimeError("Stance search produced no candidate")

    # Stress-test the selected pose in every environment with small position
    # perturbations. Track each environment's first fall rather than allowing
    # the task to reset it.
    stress_candidates = torch.clamp(
        best_pose.unsqueeze(0)
        + 0.035
        * torch.randn(
            (num_envs, num_joints),
            generator=generator,
            device=device,
        ),
        joint_floor,
        joint_ceiling,
    )
    place_candidates(stress_candidates)
    stress_actions = torch.clamp(
        (stress_candidates - robot.data.default_joint_pos.torch) / cfg.action_scale,
        -1.0,
        1.0,
    )
    still_stable = torch.ones(num_envs, dtype=torch.bool, device=device)
    first_fall_step = torch.full((num_envs,), args.stress_steps, dtype=torch.long, device=device)
    for step in range(args.stress_steps):
        env.step(stress_actions)
        height = robot.data.root_pos_w.torch[:, 2] - base_env.scene.env_origins[:, 2]
        gravity_z = robot.data.projected_gravity_b.torch[:, 2]
        stable_now = (height > 0.13) & (gravity_z < -0.65)
        newly_fallen = still_stable & ~stable_now
        first_fall_step[newly_fallen] = step
        still_stable &= stable_now

    final_height = robot.data.root_pos_w.torch[:, 2] - base_env.scene.env_origins[:, 2]
    final_gravity_z = robot.data.projected_gravity_b.torch[:, 2]
    pose_list = best_pose.tolist()
    stress = {
        "stable_fraction": still_stable.float().mean().item(),
        "mean_first_fall_seconds": (first_fall_step.float().mean() * base_env.step_dt).item(),
        "mean_final_height": final_height.mean().item(),
        "mean_final_gravity_z": final_gravity_z.mean().item(),
    }
    result = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "seed": 20260729,
        "num_envs": num_envs,
        "joint_names": list(robot.joint_names),
        "best_pose_radians": pose_list,
        "best_pose_degrees": [value * 180.0 / 3.141592653589793 for value in pose_list],
        "best_score": best_score,
        "generations": generation_records,
        "stress_test": stress,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = args.output_dir / (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-stance-search.json"
    )
    output_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"STANCE_SEARCH_RESULT={output_path}", flush=True)
    print(json.dumps(result, indent=2), flush=True)
    env.close()
finally:
    simulation_app.close()
