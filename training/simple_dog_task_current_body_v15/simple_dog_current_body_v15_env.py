"""CurrentBodyV15 environment retaining V14 observations and rewards."""

from simple_dog_task_current_body_v14.simple_dog_current_body_v14_env import (
    SimpleDogCurrentBodyV14Env,
)

from .simple_dog_current_body_v15_env_cfg import SimpleDogCurrentBodyV15HardEnvCfg


class SimpleDogCurrentBodyV15Env(SimpleDogCurrentBodyV14Env):
    """Delay recovery-force difficulty while preserving compact history."""

    cfg: SimpleDogCurrentBodyV15HardEnvCfg
