"""Bounded simulation diagnostic: can coordinated targets produce locomotion?

Uses measured sole Jacobians and the unchanged V20 actuator/control path.
This local linear controller is not a learned policy or a promotion test.
"""
import argparse
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import traceback

from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--reach", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--case-index", type=int)
parser.add_argument("--video-folder", type=Path)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
if args.output.exists():
    parser.error("refusing to overwrite diagnostic evidence")
if args.video_folder:
    if args.video_folder.exists():
        parser.error("refusing to overwrite a video directory")
    args.enable_cameras = True
app = AppLauncher(args).app

import gymnasium as gym
import torch
from isaaclab_tasks.utils import parse_env_cfg
import simple_dog_task_current_body_v20  # noqa: F401


def probe():
    reach = json.loads(args.reach.read_text())
    if not reach["passed"]:
        raise ValueError("requires a successful sole-point diagnostic")
    settings = [dict(speed=0., frequency=.9, lift=0., attitude_gain=0.)]
    settings += [dict(speed=v, frequency=f, lift=h, attitude_gain=g)
                 for v, f, h, g in itertools.product((.04, .08), (.6, .9, 1.2), (.01, .02), (0., .5))]
    if args.case_index is not None:
        if not 0 <= args.case_index < len(settings):
            raise ValueError("invalid diagnostic case index")
        settings = [settings[args.case_index]]
    count = len(settings)
    task = "Isaac-Locomotion-CurrentBodyV20-Flat-Eval-Simple-Dog-Direct-v0"
    cfg = parse_env_cfg(task, device=args.device, num_envs=count)
    cfg.seed = 42
    cfg.evaluation_segments = (("stepping_diagnostic", 2000, .08, 0., 0., 0., 0., 0.),)
    cfg.print_play_metrics = False
    cfg.episode_length_s = 30.
    env = gym.make(task, cfg=cfg, render_mode="rgb_array" if args.video_folder else None)
    if args.video_folder:
        env = gym.wrappers.RecordVideo(env, video_folder=str(args.video_folder),
                                      step_trigger=lambda step: step == 0,
                                      video_length=1000, disable_logger=True)
    base = env.unwrapped
    try:
        env.reset()
        actual_names = [base._robot.body_names[i] for i in base._feet_body_ids]
        if actual_names != [f["foot"] for f in reach["feet"]]:
            raise ValueError("sole Jacobian foot order differs from active control profile")
        device = base.device
        inverse = torch.linalg.inv(torch.tensor(
            [f["foot_jacobian_per_policy_radian"] for f in reach["feet"]], device=device))
        nominal = torch.tensor([f["nominal_tip_forward_lateral_up_m"] for f in reach["feet"]], device=device)
        values = {key: torch.tensor([s[key] for s in settings], device=device)[:, None]
                  for key in settings[0]}
        offsets = torch.tensor([0., .5, .5, 0.], device=device)[None, :]
        limits = torch.tensor(cfg.action_limit_by_joint, device=device)[None, :]
        sums = torch.zeros(count, 7, device=device)
        landings = torch.zeros(count, 4, device=device)
        air_steps = torch.zeros_like(landings)
        resets = torch.zeros(count, device=device)
        previous_contact = torch.ones(count, 4, dtype=torch.bool, device=device)
        start_position = None
        samples = 0
        with torch.inference_mode():
            for step in range(1000):
                t = step * base.step_dt
                phase = ((t - 2.) * values["frequency"] + offsets) % 1.
                duty = .6
                amplitude = values["speed"] * duty / values["frequency"] / 2.
                swing = ((phase - duty) / (1. - duty)).clamp(0., 1.)
                smooth = swing.square() * (3. - 2. * swing)
                endpoint_slope = swing - 3. * swing.square() + 2. * swing.pow(3)
                x_swing = (-amplitude + 2. * amplitude * smooth
                           - values["speed"] * (1. - duty) / values["frequency"] * endpoint_slope)
                x = torch.where(phase < duty, amplitude * (1. - 2. * phase / duty), x_swing)
                z = torch.where(phase < duty, 0., values["lift"] * torch.sin(math.pi * swing).square())
                # Use the same causal IMU estimate as the physical actor.
                gravity = base._gravity_previous
                normal_z = gravity[:, 2:3].clamp(max=-.5)
                plane = (-nominal[:, 2][None, :] - gravity[:, :2] @ nominal[:, :2].T) / normal_z
                correction = ((plane - nominal[:, 2][None, :]) * values["attitude_gain"]).clamp(-.02, .02)
                ramp = min(1., max(0., (t - 2.) / 2.))
                displacement = torch.stack((x, torch.zeros_like(x), z + correction), dim=-1) * ramp
                q = torch.einsum("lij,nlj->nli", inverse, displacement).flatten(1)
                raw = q / cfg.action_scale
                clipped = (raw.abs() > limits).float().mean(-1)
                actions = torch.maximum(torch.minimum(raw, limits), -limits)
                obs, reward, terminated, truncated, _ = env.step(actions)
                resets += terminated | truncated
                if not torch.isfinite(obs["policy"]).all():
                    raise RuntimeError("nonfinite diagnostic actor observations")
                contact = base._contact_sensor.data.current_contact_time.torch[:, base._feet_sensor_ids] > 0
                if step >= 300:
                    if start_position is None:
                        start_position = base._robot.data.root_pos_w.torch.clone()
                    motion = base._semantic_vector_b(base._robot.data.root_lin_vel_b.torch)
                    height, roll, pitch = base._body_posture()
                    sums += torch.stack((motion[:, 0], motion[:, 1].abs(), (roll.square() + pitch.square()).sqrt(),
                                         height, clipped, reward, base._actions.square().mean(-1).sqrt()), dim=-1)
                    landings += contact & ~previous_contact
                    air_steps += ~contact
                    samples += 1
                previous_contact = contact
        average = (sums / samples).cpu().tolist()
        travel = (base._robot.data.root_pos_w.torch - start_position).norm(dim=-1).cpu().tolist()
        output = []
        for i, setting in enumerate(settings):
            output.append(dict(**setting, mean_forward_m_s=average[i][0], mean_abs_lateral_m_s=average[i][1],
                               mean_tilt_rad=average[i][2], mean_height_m=average[i][3],
                               requested_joint_clip_fraction=average[i][4], mean_reward=average[i][5],
                               mean_action_rms=average[i][6], root_displacement_norm_m=travel[i],
                               landings_frflbrbl=landings[i].cpu().tolist(),
                               air_fraction_frflbrbl=(air_steps[i] / samples).cpu().tolist(), resets=int(resets[i])))
        return dict(completed=True, results=output, control_hz=50, simulation_seconds=20,
                    case_index=args.case_index,
                    measured_seconds=samples * base.step_dt, duty_fraction=.6,
                    reach_sha256=hashlib.sha256(args.reach.read_bytes()).hexdigest(),
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    profile=os.environ["SIMPLE_DOG_CONTROL_PROFILE"],
                    limitation="local linear kinematics and two diagonal phases; a diagnostic, not a deployable policy or acceptance result")
    finally:
        env.close()


try:
    result = probe()
except Exception:
    result = dict(completed=False, error=traceback.format_exc())
finally:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    app.close()
if not result["completed"]:
    raise SystemExit(1)
