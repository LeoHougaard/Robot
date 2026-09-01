"""CurrentBodyV8 environment with isolated translation and physical pushes."""

from simple_dog_task_current_body_v7.simple_dog_current_body_v7_env import (
    SimpleDogCurrentBodyV7Env,
)

from .simple_dog_current_body_v8_env_cfg import SimpleDogCurrentBodyV8HardEnvCfg


class SimpleDogCurrentBodyV8Env(SimpleDogCurrentBodyV7Env):
    """Reuse the V7 force path while changing inputs only."""

    cfg: SimpleDogCurrentBodyV8HardEnvCfg
