"""Fixed-command V21 acquisition comparison. This cannot promote a delivery policy."""
import argparse
import hashlib
import json
from pathlib import Path
import traceback

from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--checkpoint", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--num-envs", type=int, default=8)
parser.add_argument("--seed", type=int, default=42)
parser.add_argument("--video-folder", type=Path)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
if args.output.exists() or (args.video_folder and args.video_folder.exists()):
    parser.error("refusing to overwrite evidence")
if "quadruped_current_body_v21_" not in str(args.checkpoint):
    parser.error("requires an isolated V21 checkpoint")
if args.video_folder:
    args.enable_cameras = True
app = AppLauncher(args).app

import gymnasium as gym
import torch
from isaaclab_tasks.utils import parse_env_cfg
from initialize_delivery_stride import build_actor
import simple_dog_task_current_body_v21  # noqa: F401


def evaluate():
    from verify_delivery_terrain import verify
    terrain_verification = verify()
    task = "Isaac-Locomotion-CurrentBodyV21-Acquire-Simple-Dog-Direct-v0"
    cfg = parse_env_cfg(task, device=args.device, num_envs=args.num_envs)
    cfg.seed = args.seed
    cfg.episode_length_s = 30.
    assert cfg.terrain.terrain_type == "plane" and cfg.terrain.terrain_generator is None
    env = gym.make(task, cfg=cfg, render_mode="rgb_array" if args.video_folder else None)
    if args.video_folder:
        env = gym.wrappers.RecordVideo(env, video_folder=str(args.video_folder),
                                      step_trigger=lambda s: s == 0, video_length=1000, disable_logger=True)
    base = env.unwrapped
    try:
        _, actor = build_actor(Path(__file__).parent / "simple_dog_task_current_body_v21/agents/rl_games_ppo_cfg.yaml")
        state = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
        actor.load_state_dict(state["model"], strict=True)
        actor.to(base.device).eval()
        obs, _ = env.reset()
        sums = torch.zeros(args.num_envs, 6, device=base.device)
        resets = torch.zeros(args.num_envs, device=base.device)
        air = torch.zeros(args.num_envs, 4, device=base.device)
        landings = torch.zeros_like(air)
        previous_contact = torch.ones_like(air, dtype=torch.bool)
        with torch.inference_mode():
            for step in range(1000):
                assert obs["policy"].shape == (args.num_envs, 428)
                assert obs["critic"].shape == (args.num_envs, 438)
                if not torch.isfinite(obs["policy"]).all():
                    raise RuntimeError("nonfinite actor observation")
                residual = actor(dict(is_train=False, prev_actions=None,
                                      obs=obs["policy"], rnn_states=None))["mus"]
                if not torch.isfinite(residual).all():
                    raise RuntimeError("nonfinite residual")
                obs, reward, terminated, truncated, _ = env.step(residual.clamp(-1., 1.))
                resets += terminated | truncated
                contact = base._contact_sensor.data.current_contact_time.torch[:, base._feet_sensor_ids] > 0
                if step >= 300:
                    motion = base._semantic_vector_b(base._robot.data.root_lin_vel_b.torch)
                    height, roll, pitch = base._body_posture()
                    sums += torch.stack((motion[:, 0], motion[:, 1].abs(),
                                         torch.sqrt(roll.square() + pitch.square()), height, reward,
                                         (residual.abs() >= 1.).float().mean(-1)), dim=-1)
                    air += ~contact
                    landings += contact & ~previous_contact
                previous_contact = contact
        rows = []
        for i, values in enumerate((sums / 700).cpu().tolist()):
            row = dict(zip(("mean_forward_m_s", "mean_abs_lateral_m_s", "mean_tilt_rad",
                            "mean_height_m", "mean_reward", "residual_clip_fraction"), values))
            row.update(resets=int(resets[i]), landings_frflbrbl=landings[i].cpu().tolist(),
                       air_fraction_frflbrbl=(air[i] / 700).cpu().tolist())
            rows.append(row)
        return dict(completed=True, checkpoint_sha256=hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    seed=args.seed, control_hz=50, simulation_seconds=20, measured_seconds=14,
                    terrain="plane", terrain_verification=terrain_verification, command=[.04, 0., 0.], results=rows,
                    limitation="acquisition comparison only; no turning, stopping, terrain or deployment acceptance")
    finally:
        env.close()


try:
    result = evaluate()
except Exception:
    result = dict(completed=False, error=traceback.format_exc())
finally:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    app.close()
if not result["completed"]:
    raise SystemExit(1)
