"""Verify terrain-height sensing and actor shape in the actual Isaac environment.

Run sequentially after other GPU jobs exit. This tests simulation measurement,
not policy quality or physical walking.
"""
import argparse
import json
import traceback
from pathlib import Path

from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--surface", choices=("flat", "uneven"), required=True)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
app = AppLauncher(args).app

import gymnasium as gym
import torch
import simple_dog_task_current_body_v20  # noqa: F401
from isaaclab_tasks.utils import parse_env_cfg


def inspect(task, flat):
    cfg = parse_env_cfg(task, device=args.device, num_envs=4)
    cfg.evaluation_segments = ()
    cfg.print_play_metrics = False
    env = gym.make(task, cfg=cfg)
    base = env.unwrapped
    try:
        observation, _ = env.reset()
        measurements = []
        with torch.inference_mode():
            for step in range(150):
                observation, reward, terminated, truncated, _ = env.step(
                    torch.zeros(4, 12, device=base.device))
                assert observation["policy"].shape == (4, 426)
                assert torch.isfinite(observation["policy"]).all()
                assert torch.isfinite(reward).all()
                hits = base._height_scanner.data.ray_hits_w.torch[..., 2]
                assert torch.isfinite(hits).all()
                assert torch.allclose(base._height_scanner.data.pos_w.torch,
                                      base._robot.data.root_pos_w.torch, atol=1e-4), \
                    "height scanner must follow each moving robot, not its initial USD pose"
                if flat:
                    assert torch.allclose(hits, torch.zeros_like(hits), atol=1e-5)
                if step >= 50:
                    height, roll, pitch = base._body_posture()
                    measurements.append(torch.stack((height, roll, pitch), dim=-1).cpu())
            if not flat:
                print("uneven ray height range", hits.min().item(), hits.max().item(), flush=True)
                assert hits.max() - hits.min() >= .0005, "uneven test selected flat terrain"
        state = torch.stack(measurements)
        return dict(task=task, observation_shape=list(observation["policy"].shape),
                    ray_count=hits.shape[1], terrain_z_min=hits.min().item(),
                    terrain_z_max=hits.max().item(),
                    mean_height_roll_pitch=state.mean(dim=(0, 1)).tolist(),
                    min_height=state[..., 0].min().item(), max_height=state[..., 0].max().item(),
                    final_root_z=base._robot.data.root_pos_w.torch[:, 2].cpu().tolist())
    finally:
        env.close()


try:
    # Each terrain runs in its own Kit process, matching real evaluations and
    # avoiding cross-stage reuse of RayCaster's global static mesh cache.
    flat = args.surface == "flat"
    task = ("Isaac-Locomotion-CurrentBodyV20-Flat-Eval-Simple-Dog-Direct-v0" if flat
            else "Isaac-Locomotion-CurrentBodyV20-Eval-Simple-Dog-Direct-v0")
    results = [inspect(task, flat)]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(passed=True, checks=results), indent=2) + "\n")
    print(json.dumps(results), flush=True)
except Exception:
    error = traceback.format_exc()
    print(error, flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(dict(passed=False, error=error), indent=2) + "\n")
finally:
    app.close()
