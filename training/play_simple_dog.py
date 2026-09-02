"""Register the local dog task, then hand off to Isaac Lab's RL-Games player."""

import faulthandler
import os
import runpy
import signal

import simple_dog_task  # noqa: F401
import simple_dog_task_v2  # noqa: F401
if os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_v3":
    import simple_dog_task_current  # noqa: F401
elif os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_body_v4":
    import simple_dog_task_current_body_v4  # noqa: F401
elif os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_body_v5":
    import simple_dog_task_current_body_v4  # noqa: F401
    import simple_dog_task_current_body_v5  # noqa: F401
elif os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_body_v6":
    import simple_dog_task_current_body_v4  # noqa: F401
    import simple_dog_task_current_body_v5  # noqa: F401
    import simple_dog_task_current_body_v6  # noqa: F401
elif os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_body_v7":
    import simple_dog_task_current_body_v4  # noqa: F401
    import simple_dog_task_current_body_v5  # noqa: F401
    import simple_dog_task_current_body_v7  # noqa: F401
elif os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_body_v8":
    import simple_dog_task_current_body_v4  # noqa: F401
    import simple_dog_task_current_body_v5  # noqa: F401
    import simple_dog_task_current_body_v7  # noqa: F401
    import simple_dog_task_current_body_v8  # noqa: F401
elif os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_body_v9":
    import simple_dog_task_current_body_v4  # noqa: F401
    import simple_dog_task_current_body_v5  # noqa: F401
    import simple_dog_task_current_body_v7  # noqa: F401
    import simple_dog_task_current_body_v9  # noqa: F401
elif os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_body_v10":
    import simple_dog_task_current_body_v4  # noqa: F401
    import simple_dog_task_current_body_v5  # noqa: F401
    import simple_dog_task_current_body_v7  # noqa: F401
    import simple_dog_task_current_body_v10  # noqa: F401
elif os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_body_v11":
    import simple_dog_task_current_body_v4  # noqa: F401
    import simple_dog_task_current_body_v5  # noqa: F401
    import simple_dog_task_current_body_v7  # noqa: F401
    import simple_dog_task_current_body_v10  # noqa: F401
    import simple_dog_task_current_body_v11  # noqa: F401
elif os.environ.get("SIMPLE_DOG_POLICY_FAMILY") == "current_body_v12":
    import simple_dog_task_current_body_v4  # noqa: F401
    import simple_dog_task_current_body_v5  # noqa: F401
    import simple_dog_task_current_body_v7  # noqa: F401
    import simple_dog_task_current_body_v10  # noqa: F401
    import simple_dog_task_current_body_v11  # noqa: F401
    import simple_dog_task_current_body_v12  # noqa: F401
from robot_control_profile import apply_agent_profile, load_control_profile

import isaaclab_tasks.utils as isaac_task_utils


control_profile = load_control_profile()
original_resolve_task_config = isaac_task_utils.resolve_task_config


def resolve_task_config_with_profile(*args, **kwargs):
    env_cfg, agent_cfg = original_resolve_task_config(*args, **kwargs)
    return env_cfg, apply_agent_profile(agent_cfg, control_profile)


isaac_task_utils.resolve_task_config = resolve_task_config_with_profile

# A non-fatal USR1 signal produces a Python stack dump in container logs.  This
# keeps startup diagnosis evidence-based without enabling ptrace or running the
# container as root.
faulthandler.register(signal.SIGUSR1, all_threads=True)


try:
    runpy.run_path(
        "/workspace/isaaclab/scripts/reinforcement_learning/rl_games/play.py",
        run_name="__main__",
    )
finally:
    isaac_task_utils.resolve_task_config = original_resolve_task_config
