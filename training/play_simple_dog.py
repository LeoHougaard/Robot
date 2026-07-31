"""Register the local dog task, then hand off to Isaac Lab's RL-Games player."""

import faulthandler
import runpy
import signal

import simple_dog_task  # noqa: F401
import simple_dog_task_v2  # noqa: F401

# A non-fatal USR1 signal produces a Python stack dump in container logs.  This
# keeps startup diagnosis evidence-based without enabling ptrace or running the
# container as root.
faulthandler.register(signal.SIGUSR1, all_threads=True)


runpy.run_path(
    "/workspace/isaaclab/scripts/reinforcement_learning/rl_games/play.py",
    run_name="__main__",
)
