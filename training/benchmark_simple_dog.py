"""Bounded environment throughput/profile check using the installed Isaac Lab 3 API."""
import argparse
import cProfile
import importlib
import io
import json
import pstats
import sys
import time
from collections import defaultdict
import faulthandler
import signal
from pathlib import Path

faulthandler.register(signal.SIGUSR1, all_threads=True)

import gymnasium as gym
import torch
from isaaclab_tasks.utils import add_launcher_args, launch_simulation, resolve_task_config, setup_preset_cli

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--task", required=True)
parser.add_argument("--family", default="simple_dog_task_current_body_v18")
parser.add_argument("--num_envs", type=int, default=128)
parser.add_argument("--steps", type=int, default=32)
parser.add_argument("--output", type=Path, required=True)
add_launcher_args(parser)
args, hydra_args = setup_preset_cli(parser)
sys.argv = [sys.argv[0]] + hydra_args
importlib.import_module(args.family)
print("BENCHMARK resolving task configuration", flush=True)
cfg, _ = resolve_task_config(args.task, "rl_games_cfg_entry_point")
cfg.scene.num_envs = args.num_envs
cfg.seed = 42
print("BENCHMARK launching simulation", flush=True)
with launch_simulation(cfg, args):
    env = gym.make(args.task, cfg=cfg)
    base = env.unwrapped
    observations, _ = env.reset()
    action = torch.zeros(args.num_envs, 12, device=base.device)
    for _ in range(8):
        env.step(action)
    torch.cuda.synchronize()
    timings = defaultdict(float)
    def instrument(owner, name, label):
        original = getattr(owner, name)
        def measured(*a, **kw):
            before = time.perf_counter()
            result = original(*a, **kw)
            torch.cuda.synchronize()
            timings[label] += time.perf_counter() - before
            return result
        setattr(owner, name, measured)
    for name in ("_pre_physics_step", "_apply_action", "_get_rewards", "_get_dones", "_get_observations"):
        instrument(base, name, name)
    for owner, names, prefix in ((base.sim, ("step", "render"), "sim."),
                                  (base.scene, ("write_data_to_sim", "update"), "scene.")):
        for name in names:
            instrument(owner, name, prefix + name)
    profile = cProfile.Profile()
    started = time.perf_counter()
    profile.enable()
    with torch.inference_mode():
        for _ in range(args.steps):
            env.step(action)
    torch.cuda.synchronize()
    profile.disable()
    elapsed = time.perf_counter() - started
    stream = io.StringIO()
    pstats.Stats(profile, stream=stream).sort_stats("cumulative").print_stats(40)
    report = dict(task=args.task, num_envs=args.num_envs, steps=args.steps, elapsed_s=elapsed,
                  transitions_per_s=args.num_envs * args.steps / elapsed, torch_threads=torch.get_num_threads(),
                  body_count=base._robot.num_bodies, joint_count=base._robot.num_joints,
                  synchronized_component_seconds=dict(timings),
                  profile=stream.getvalue())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print("BENCHMARK " + json.dumps(report), flush=True)
    env.close()
