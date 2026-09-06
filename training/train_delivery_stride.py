"""V21 RL-Games training with the verified AppLauncher-first startup order.

Uses Isaac Lab's installed RL-Games wrapper and observer, matching its stock
trainer's configuration, logging and Runner flow. Initialize Kit before
importing task/sensor/physics modules, as in the successful diagnostics.
"""
import argparse
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import time

from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--task", required=True)
parser.add_argument("--num_envs", type=int, required=True)
parser.add_argument("--max_iterations", type=int, required=True)
parser.add_argument("--video", action="store_true")
parser.add_argument("--video_interval", type=int, default=5000)
parser.add_argument("--video_length", type=int, default=400)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
if args.task != "Isaac-Locomotion-CurrentBodyV21-Acquire-Simple-Dog-Direct-v0":
    parser.error("this entry point only supports the reviewed V21 acquisition task")
if args.video:
    args.enable_cameras = True
app = AppLauncher(args).app

import gymnasium as gym
import yaml
from rl_games.common import env_configurations, vecenv
from rl_games.common.algo_observer import IsaacAlgoObserver
from rl_games.torch_runner import Runner
from isaaclab.utils.io import dump_yaml
from isaaclab_tasks.utils import parse_env_cfg
from isaaclab_rl.rl_games import RlGamesGpuEnv, RlGamesVecEnvWrapper
from delivery_checkpointing import DeliveryA2CAgent
from robot_control_profile import apply_agent_profile, load_control_profile
import simple_dog_task_current_body_v21  # noqa: F401


def train():
    root = Path(__file__).parent
    agent = yaml.safe_load((root / "simple_dog_task_current_body_v21/agents/rl_games_ppo_cfg.yaml").read_text())
    agent = apply_agent_profile(agent, load_control_profile())
    config = agent["params"]["config"]
    config["max_epochs"] = args.max_iterations
    cfg = parse_env_cfg(args.task, device=args.device, num_envs=args.num_envs)
    cfg.seed = agent["params"]["seed"]
    assert cfg.terrain.terrain_type == "plane" and cfg.terrain.terrain_generator is None
    assert cfg.observation_space == 428 and cfg.state_space == 438
    config["train_dir"] = str(Path("logs/rl_games", config["name"]).resolve())
    config["full_experiment_name"] = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    experiment = Path(config["train_dir"], config["full_experiment_name"])
    if experiment.exists():
        raise ValueError("refusing to reuse an experiment directory")
    cfg.log_dir = str(experiment)
    dump_yaml(str(experiment / "params/env.yaml"), cfg)
    dump_yaml(str(experiment / "params/agent.yaml"), agent)
    print(f"Exact experiment name requested from command line: {experiment}", flush=True)
    env = gym.make(args.task, cfg=cfg, render_mode="rgb_array" if args.video else None)
    if args.video:
        env = gym.wrappers.RecordVideo(env, video_folder=str(experiment / "videos/train"),
                                      step_trigger=lambda s: s % args.video_interval == 0,
                                      video_length=args.video_length, disable_logger=True)
    try:
        wrapper = RlGamesVecEnvWrapper(env, config["device"], math.inf, agent["params"]["env"]["clip_actions"])
        vecenv.register("IsaacRlgWrapper", lambda config_name, num_actors, **kw: RlGamesGpuEnv(config_name, num_actors, **kw))
        env_configurations.register("rlgpu", {"vecenv_type": "IsaacRlgWrapper", "env_creator": lambda **kw: wrapper})
        config["num_actors"] = env.unwrapped.num_envs
        runner = Runner(IsaacAlgoObserver())
        runner.algo_factory.register_builder("a2c_continuous", lambda **kw: DeliveryA2CAgent(**kw))
        runner.load(agent)
        runner.reset()
        checkpoint = os.environ.get("SIMPLE_DOG_CHECKPOINT", "")
        if not checkpoint or "quadruped_current_body_v21_" not in checkpoint:
            raise ValueError("requires a preserved V21 initialization or continuation")
        start = time.monotonic()
        runner.run(dict(train=True, play=False, sigma=None, checkpoint=checkpoint))
        print(f"Training time: {time.monotonic() - start:.2f} seconds", flush=True)
    finally:
        env.close()


try:
    train()
finally:
    app.close()
