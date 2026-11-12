"""CurrentBodyV9 environment with mixed inputs and physical pushes."""

from simple_dog_task_current_body_v7.simple_dog_current_body_v7_env import (
    SimpleDogCurrentBodyV7Env,
)

from .simple_dog_current_body_v9_env_cfg import SimpleDogCurrentBodyV9HardEnvCfg


class SimpleDogCurrentBodyV9Env(SimpleDogCurrentBodyV7Env):
    """Keep V7 physics so V9 isolates the PPO exploration hypothesis."""

    cfg: SimpleDogCurrentBodyV9HardEnvCfg
