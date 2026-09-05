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
parser.add_argument("--command-sweep", action="store_true",
                    help="Probe signed planar commands and score their actual reward components.")
parser.add_argument("--dataset", type=Path,
                    help="Optionally preserve causal actor observations and reference actions for an initialization experiment.")
parser.add_argument("--checkpoint", type=Path,
                    help="Run a learned 426-input actor instead of reference targets; diagnostic only.")
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
if args.output.exists():
    parser.error("refusing to overwrite diagnostic evidence")
if args.dataset and args.dataset.exists():
    parser.error("refusing to overwrite a demonstration dataset")
if args.checkpoint and (not args.command_sweep or args.dataset):
    parser.error("actor diagnostics require --command-sweep and cannot generate teacher data")
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
    if args.command_sweep:
        commands = [("forward_slow", .04, 0., 0.), ("forward", .08, 0., 0.),
                    ("reverse_slow", -.04, 0., 0.), ("reverse", -.08, 0., 0.),
                    ("left", 0., .04, 0.), ("right", 0., -.04, 0.),
                    ("turn_left", 0., 0., .2), ("turn_right", 0., 0., -.2),
                    ("diagonal", .04, .04, 0.), ("curve", .04, 0., .15)]
        settings = [dict(name="stand", forward=0., lateral=0., yaw=0., frequency=.6,
                         lift=0., attitude_gain=0.)]
        settings += [dict(name=n, forward=x, lateral=y, yaw=w, frequency=f,
                          lift=.02, attitude_gain=.5)
                     for n, x, y, w in commands for f in (.6, .9)]
    if args.case_index is not None:
        if not 0 <= args.case_index < len(settings):
            raise ValueError("invalid diagnostic case index")
        settings = [settings[args.case_index]]
    count = len(settings)
    task = "Isaac-Locomotion-CurrentBodyV20-Flat-Eval-Simple-Dog-Direct-v0"
    cfg = parse_env_cfg(task, device=args.device, num_envs=count)
    cfg.seed = 42
    cfg.evaluation_segments = (("stepping_diagnostic", 2000, .08, 0., 0., 0., 0., 0.),)
    if args.command_sweep:
        cfg.evaluation_segments = ()
        cfg.pose_goal_training = False
    cfg.print_play_metrics = False
    cfg.episode_length_s = 30.
    env = gym.make(task, cfg=cfg, render_mode="rgb_array" if args.video_folder else None)
    if args.video_folder:
        env = gym.wrappers.RecordVideo(env, video_folder=str(args.video_folder),
                                      step_trigger=lambda step: step == 0,
                                      video_length=1000, disable_logger=True)
    base = env.unwrapped
    try:
        obs, _ = env.reset()
        actual_names = [base._robot.body_names[i] for i in base._feet_body_ids]
        if actual_names != [f["foot"] for f in reach["feet"]]:
            raise ValueError("sole Jacobian foot order differs from active control profile")
        device = base.device
        actor = None
        if args.checkpoint:
            from bootstrap_delivery_actor import new_model
            _, actor = new_model(Path(__file__).parent / "simple_dog_task_current_body_v20/agents/rl_games_ppo_cfg.yaml")
            checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
            actor.load_state_dict(checkpoint["model"], strict=True)
            actor.to(device).eval()
        inverse = torch.linalg.inv(torch.tensor(
            [f["foot_jacobian_per_policy_radian"] for f in reach["feet"]], device=device))
        nominal = torch.tensor([f["nominal_tip_forward_lateral_up_m"] for f in reach["feet"]], device=device)
        values = {key: torch.tensor([s[key] for s in settings], device=device)[:, None]
                  for key in settings[0] if key != "name"}
        requested = (torch.cat([values[k] for k in ("forward", "lateral", "yaw")], dim=1)
                     if args.command_sweep else None)
        offsets = torch.tensor([0., .5, .5, 0.], device=device)[None, :]
        limits = torch.tensor(cfg.action_limit_by_joint, device=device)[None, :]
        sums = torch.zeros(count, 7, device=device)
        motion_sums = torch.zeros(count, 5, device=device)
        component_names = ("body_tracking", "body_motion_shortfall", "opposite_leg_sync")
        component_sums = torch.zeros(count, len(component_names), device=device)
        landings = torch.zeros(count, 4, device=device)
        air_steps = torch.zeros_like(landings)
        resets = torch.zeros(count, device=device)
        previous_contact = torch.ones(count, 4, dtype=torch.bool, device=device)
        start_position = None
        samples = 0
        observation_samples, action_samples = [], []
        with torch.inference_mode():
            for step in range(1000):
                t = step * base.step_dt
                if args.command_sweep:
                    # Only command generation differs. Keep the production
                    # sensor, action, actuator and reward path intact.
                    base._command_targets.copy_(requested)
                    base._commands.copy_(requested)
                    base._command_steps_remaining.fill_(2000)
                    base._posture_targets.zero_()
                    base._posture_commands.zero_()
                phase = ((t - 2.) * values["frequency"] + offsets) % 1.
                duty = .6
                vx = (values["forward"] - values["yaw"] * nominal[:, 1][None, :]
                      if args.command_sweep else values["speed"])
                vy = (values["lateral"] + values["yaw"] * nominal[:, 0][None, :]
                      if args.command_sweep else torch.zeros_like(vx))
                amplitude = vx * duty / values["frequency"] / 2.
                swing = ((phase - duty) / (1. - duty)).clamp(0., 1.)
                smooth = swing.square() * (3. - 2. * swing)
                endpoint_slope = swing - 3. * swing.square() + 2. * swing.pow(3)
                x_swing = (-amplitude + 2. * amplitude * smooth
                           - vx * (1. - duty) / values["frequency"] * endpoint_slope)
                x = torch.where(phase < duty, amplitude * (1. - 2. * phase / duty), x_swing)
                amplitude_y = vy * duty / values["frequency"] / 2.
                y_swing = (-amplitude_y + 2. * amplitude_y * smooth
                           - vy * (1. - duty) / values["frequency"] * endpoint_slope)
                y = torch.where(phase < duty, amplitude_y * (1. - 2. * phase / duty), y_swing)
                z = torch.where(phase < duty, 0., values["lift"] * torch.sin(math.pi * swing).square())
                # Use the same causal IMU estimate as the physical actor.
                gravity = base._gravity_previous
                normal_z = gravity[:, 2:3].clamp(max=-.5)
                plane = (-nominal[:, 2][None, :] - gravity[:, :2] @ nominal[:, :2].T) / normal_z
                correction = ((plane - nominal[:, 2][None, :]) * values["attitude_gain"]).clamp(-.02, .02)
                ramp = min(1., max(0., (t - 2.) / 2.))
                displacement = torch.stack((x, y, z + correction), dim=-1) * ramp
                q = torch.einsum("lij,nlj->nli", inverse, displacement).flatten(1)
                raw = q / cfg.action_scale
                clipped = (raw.abs() > limits).float().mean(-1)
                actions = torch.maximum(torch.minimum(raw, limits), -limits)
                if actor is not None:
                    # The reference above supplies no input or action to the
                    # actor. It must sustain motion from causal sensors alone.
                    raw = actor(dict(is_train=False, prev_actions=None,
                                     obs=obs["policy"], rnn_states=None))["mus"]
                    if not torch.isfinite(raw).all():
                        raise RuntimeError("nonfinite learned actor action")
                    clipped = (raw.abs() > limits).float().mean(-1)
                    actions = raw.clamp(-1., 1.)
                if args.dataset and step >= 100:
                    # The next target uses the last returned observation.
                    # Do not advance the sensor/history builder an extra time.
                    observation_samples.append(obs["policy"].clone())
                    action_samples.append(actions.clone())
                previous_components = torch.stack([base._episode_sums[k].clone() for k in component_names], dim=-1)
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
                    gyro = base._semantic_vector_b(base._robot.data.root_ang_vel_b.torch)
                    motion_sums += torch.stack((motion[:, 1], gyro[:, 2], roll, pitch,
                                                motion[:, 2].square()), dim=-1)
                    component_sums += (torch.stack([base._episode_sums[k] for k in component_names], dim=-1)
                                       - previous_components)
                    if args.command_sweep and not torch.allclose(base._commands, requested, atol=1e-6):
                        raise RuntimeError("diagnostic command was overwritten by environment command generation")
                    sums += torch.stack((motion[:, 0], motion[:, 1].abs(), (roll.square() + pitch.square()).sqrt(),
                                         height, clipped, reward, base._actions.square().mean(-1).sqrt()), dim=-1)
                    landings += contact & ~previous_contact
                    air_steps += ~contact
                    samples += 1
                previous_contact = contact
        average = (sums / samples).cpu().tolist()
        motion_average = (motion_sums / samples).cpu().tolist()
        component_average = (component_sums / (samples * base.step_dt)).cpu().tolist()
        travel = (base._robot.data.root_pos_w.torch - start_position).norm(dim=-1).cpu().tolist()
        output = []
        for i, setting in enumerate(settings):
            output.append(dict(**setting, mean_forward_m_s=average[i][0], mean_abs_lateral_m_s=average[i][1],
                               mean_tilt_rad=average[i][2], mean_height_m=average[i][3],
                               requested_joint_clip_fraction=average[i][4], mean_reward=average[i][5],
                               mean_action_rms=average[i][6], root_displacement_norm_m=travel[i],
                               landings_frflbrbl=landings[i].cpu().tolist(),
                               air_fraction_frflbrbl=(air_steps[i] / samples).cpu().tolist(), resets=int(resets[i])))
            output[-1].update(mean_lateral_m_s=motion_average[i][0], mean_yaw_rad_s=motion_average[i][1],
                              mean_roll_rad=motion_average[i][2], mean_pitch_rad=motion_average[i][3],
                              vertical_speed_rms_m_s=math.sqrt(motion_average[i][4]),
                              reward_component_rates=dict(zip(component_names, component_average[i])),
                              reward_components_valid=not bool(resets[i]))
        dataset_sha = None
        if args.dataset:
            args.dataset.parent.mkdir(parents=True, exist_ok=True)
            torch.save(dict(observations=torch.stack(observation_samples).cpu(),
                            actions=torch.stack(action_samples).cpu(), settings=settings,
                            results=output, dt=base.step_dt, first_step=100,
                            actor_inputs="causal 426-value observation returned before selecting the recorded target",
                            source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()), args.dataset)
            dataset_sha = hashlib.sha256(args.dataset.read_bytes()).hexdigest()
        return dict(completed=True, results=output, control_hz=50, simulation_seconds=20,
                    case_index=args.case_index,
                    command_sweep=args.command_sweep,
                    dataset_sha256=dataset_sha,
                    checkpoint_sha256=(hashlib.sha256(args.checkpoint.read_bytes()).hexdigest()
                                       if args.checkpoint else None),
                    controller="learned_actor" if actor is not None else "scripted_reference",
                    measured_seconds=samples * base.step_dt, duty_fraction=.6,
                    reach_sha256=hashlib.sha256(args.reach.read_bytes()).hexdigest(),
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    profile=os.environ["SIMPLE_DOG_CONTROL_PROFILE"],
                    limitation=("learned actor with fixed commands; a diagnostic, not a full acceptance result"
                                if actor is not None else
                                "local linear kinematics and two diagonal phases; a diagnostic, not a deployable policy or acceptance result"))
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
