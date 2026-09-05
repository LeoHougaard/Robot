"""Measure local foot-tip reach and motor-speed headroom with the body fixed.

This unloaded simulation diagnostic is not a gait or hardware acceptance test.
It uses a material point on each neutral sole, not the lower-leg COM. The
linear speed calculation omits acceleration, contact and loaded tracking loss.
"""
import argparse
import json
import os
from pathlib import Path
import traceback

from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
if args.output.exists():
    parser.error("refusing to overwrite existing diagnostic evidence")
app = AppLauncher(args).app

import gymnasium as gym
import numpy as np
import torch
from pxr import Gf, Usd, UsdGeom, UsdPhysics
from isaaclab.utils.math import quat_apply, quat_apply_inverse
from isaaclab_tasks.utils import parse_env_cfg
import simple_dog_task_current_body_v20  # noqa: F401


def sole_points(asset, body_names, up_axis):
    stage = Usd.Stage.Open(asset)
    root = stage.GetDefaultPrim()
    cache = UsdGeom.XformCache()
    result = []
    for name in body_names:
        link = stage.GetPrimAtPath(root.GetPath().AppendPath("links/" + name))
        if not link:
            raise ValueError(f"missing foot link {name}")
        coordinates, heights = [], []
        for prim in Usd.PrimRange(link):
            if not prim.IsA(UsdGeom.Mesh) or "/collisions/" not in str(prim.GetPath()):
                continue
            points = np.asarray(UsdGeom.Mesh(prim).GetPointsAttr().Get(), dtype=float)
            points = np.column_stack((points, np.ones(len(points))))
            in_link = points @ np.asarray(cache.ComputeRelativeTransform(prim, link)[0])
            in_root = points @ np.asarray(cache.ComputeRelativeTransform(prim, root)[0])
            coordinates.append(in_link[:, :3])
            heights.append(in_root[:, :3] @ up_axis)
        if not coordinates:
            raise ValueError(f"no collision mesh points for {name}")
        coordinates, heights = np.concatenate(coordinates), np.concatenate(heights)
        result.append(coordinates[heights <= heights.min() + .0005].mean(axis=0))
    return np.asarray(result)


def inspect():
    profile = json.loads(Path(os.environ["SIMPLE_DOG_CONTROL_PROFILE"]).read_text())
    task = "Isaac-Locomotion-CurrentBodyV20-Flat-Eval-Simple-Dog-Direct-v0"
    cfg = parse_env_cfg(task, device=args.device, num_envs=49)
    cfg.evaluation_segments = ()
    cfg.print_play_metrics = False
    cfg.robot.init_state.pos = (0., 0., .45)
    spawn = cfg.robot.spawn.func

    def spawn_fixed(*spawn_args, **spawn_kwargs):
        # The publisher puts ArticulationRootAPI on a container Xform rather
        # than the base body. Isaac's generic fix_root_link cannot resolve it.
        # Add a diagnostic world joint to the explicitly profiled base link.
        prim = spawn(*spawn_args, **spawn_kwargs)
        stage = prim.GetStage()
        body = stage.GetPrimAtPath(prim.GetPath().AppendPath("links/" + cfg.base_contact_pattern))
        transform = UsdGeom.XformCache().GetLocalToWorldTransform(body)
        joint = UsdPhysics.FixedJoint.Define(stage, prim.GetPath().AppendChild("delivery_probe_anchor"))
        joint.CreateBody1Rel().SetTargets([body.GetPath()])
        joint.CreateLocalPos0Attr().Set(Gf.Vec3f(transform.ExtractTranslation()))
        joint.CreateLocalRot0Attr().Set(Gf.Quatf(transform.ExtractRotationQuat()))
        return prim

    cfg.robot.spawn.func = spawn_fixed
    cfg.episode_length_s = 30.
    cfg.termination_height = -1.
    env = gym.make(task, cfg=cfg)
    base = env.unwrapped
    try:
        env.reset()
        robot = base._robot
        feet = list(base._feet_body_ids)
        names = [robot.body_names[i] for i in feet]
        up = np.asarray(profile["robot"]["up_axis"], dtype=float)
        forward = np.asarray(profile["robot"]["forward_axis"], dtype=float)
        up /= np.linalg.norm(up)
        forward /= np.linalg.norm(forward)
        axes = torch.tensor(np.stack((forward, np.cross(up, forward), up)),
                            device=base.device, dtype=torch.float32)
        local = torch.tensor(sole_points(profile["robot"]["asset_usd"], names, up),
                             device=base.device, dtype=torch.float32)
        targets = torch.zeros(49, 12, device=base.device)
        for joint in range(12):
            targets[1 + 2 * joint, joint] = .05
            targets[2 + 2 * joint, joint] = -.05
            extent = cfg.action_scale * cfg.action_limit_by_joint[joint]
            targets[25 + 2 * joint, joint] = extent
            targets[26 + 2 * joint, joint] = -extent
        samples, positions = [], []
        initial_root = robot.data.root_pos_w.torch.clone()
        with torch.inference_mode():
            for step in range(250):
                obs, reward, terminated, truncated, _ = env.step(targets / cfg.action_scale)
                if terminated.any() or truncated.any():
                    raise RuntimeError("reach diagnostic reset unexpectedly")
                if not torch.isfinite(obs["policy"]).all():
                    raise RuntimeError("nonfinite reach observation")
                if step >= 225:
                    tip = robot.data.body_pos_w.torch[:, feet] + quat_apply(
                        robot.data.body_quat_w.torch[:, feet], local.expand(49, -1, -1))
                    relative = quat_apply_inverse(robot.data.root_quat_w.torch[:, None, :].expand(-1, 4, -1),
                                                  tip - robot.data.root_pos_w.torch[:, None, :])
                    samples.append((relative @ axes.T).cpu())
                    positions.append(base._get_policy_joint_state()[0].cpu())
        tip = torch.stack(samples).mean(0).numpy()
        q = torch.stack(positions).mean(0).numpy()
        target_error = float(np.max(np.abs(q - targets.cpu().numpy())))
        root_drift = float((robot.data.root_pos_w.torch - initial_root).norm(dim=-1).max())
        if root_drift > .001:
            raise RuntimeError(f"fixed body moved {root_drift} m")
        if target_error > .025:
            raise RuntimeError(f"unloaded joint targets not reached: maximum error {target_error} rad")
        records = []
        inverse_coupling = np.array([[1., 0., 0.], [0., 1., 0.], [0., -1., 1.]])
        speed = base._servo_trajectory.speed_nominal.cpu().numpy()
        for leg, name in enumerate(names):
            ids = range(3 * leg, 3 * leg + 3)
            jacobian = np.column_stack([(tip[1 + 2*j, leg] - tip[2 + 2*j, leg]) / .1 for j in ids])
            motor_jacobian = jacobian @ inverse_coupling
            rates = np.linalg.solve(motor_jacobian, np.array([.08, 0., 0.]))
            records.append(dict(foot=name, nominal_tip_forward_lateral_up_m=tip[0, leg].tolist(),
                                foot_jacobian_per_policy_radian=jacobian.tolist(),
                                motor_rates_for_horizontal_008_m_s=rates.tolist(),
                                motor_speed_limits_rad_s=speed[3*leg:3*leg+3].tolist(),
                                speed_utilization=float(np.max(np.abs(rates) / speed[3*leg:3*leg+3])),
                                positive_axis_tip_positions=tip[[25+2*j for j in ids], leg].tolist(),
                                negative_axis_tip_positions=tip[[26+2*j for j in ids], leg].tolist()))
        return dict(passed=True, maximum_joint_target_error_rad=target_error, root_drift_m=root_drift,
                    sole_points_in_link_frame=local.cpu().tolist(), feet=records,
                    limitation="unloaded local linear kinematics; no claim of gait feasibility or real-world speed")
    finally:
        env.close()


try:
    result = inspect()
except Exception:
    result = dict(passed=False, error=traceback.format_exc())
finally:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    app.close()
if not result["passed"]:
    raise SystemExit(1)
