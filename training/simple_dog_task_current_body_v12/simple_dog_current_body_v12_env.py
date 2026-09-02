"""CurrentBodyV12 environment retaining V11 physics and rewards."""

from simple_dog_task_current_body_v11.simple_dog_current_body_v11_env import (
    SimpleDogCurrentBodyV11Env,
)

from .simple_dog_current_body_v12_env_cfg import SimpleDogCurrentBodyV12HardEnvCfg


class SimpleDogCurrentBodyV12Env(SimpleDogCurrentBodyV11Env):
    """Isolate PPO exploration from the staged V11 environment."""

    cfg: SimpleDogCurrentBodyV12HardEnvCfg
