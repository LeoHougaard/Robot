"""Register the local dog task, then hand off to Isaac Lab's RL-Games trainer."""

import faulthandler
import builtins
import os
import runpy
import signal
import sys


faulthandler.register(signal.SIGUSR1, all_threads=True)

import simple_dog_task  # noqa: F401
import simple_dog_task_v2  # noqa: F401
if os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_v3":
    import simple_dog_task_current  # noqa: F401
from robot_control_profile import apply_agent_profile, load_control_profile


control_profile = load_control_profile()
import isaaclab_tasks.utils as isaac_task_utils

original_resolve_task_config = isaac_task_utils.resolve_task_config


def resolve_task_config_with_profile(*args, **kwargs):
    env_cfg, agent_cfg = original_resolve_task_config(*args, **kwargs)
    return env_cfg, apply_agent_profile(agent_cfg, control_profile)


isaac_task_utils.resolve_task_config = resolve_task_config_with_profile


deferred_checkpoint = os.environ.pop("SIMPLE_DOG_CHECKPOINT", "")
if deferred_checkpoint:
    # Isaac's stock trainer places the checkpoint into agent configuration
    # before gym.make(), even though RL-Games restores it only in Runner.run().
    # On GB10 that resume configuration makes this generated rough scene's
    # SimulationContext.reset() unreliable. Inject the same validated local
    # checkpoint at Runner.run instead: the environment and PhysX initialize
    # as a fresh job, then RL-Games restores policy/normalization/epoch before
    # collecting a single training frame.
    original_import = builtins.__import__

    def import_with_deferred_restore(name, globals=None, locals=None, fromlist=(), level=0):
        module = original_import(name, globals, locals, fromlist, level)
        if name == "rl_games.torch_runner":
            torch_runner = sys.modules[name]
            if not getattr(torch_runner.Runner, "_simple_dog_deferred", False):
                original_runner_run = torch_runner.Runner.run

                def run_with_deferred_checkpoint(self, args):
                    deferred_args = dict(args)
                    deferred_args["checkpoint"] = deferred_checkpoint
                    return original_runner_run(self, deferred_args)

                torch_runner.Runner.run = run_with_deferred_checkpoint
                torch_runner.Runner._simple_dog_deferred = True
        return module

    builtins.__import__ = import_with_deferred_restore


try:
    runpy.run_path(
        "/workspace/isaaclab/scripts/reinforcement_learning/rl_games/train.py",
        run_name="__main__",
    )
finally:
    isaac_task_utils.resolve_task_config = original_resolve_task_config
    if deferred_checkpoint:
        builtins.__import__ = original_import
