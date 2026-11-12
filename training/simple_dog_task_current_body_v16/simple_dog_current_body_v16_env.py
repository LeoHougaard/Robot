"""CurrentBodyV16 environment retaining V15 observations and physics."""

from simple_dog_task_current_body_v15.simple_dog_current_body_v15_env import (
    SimpleDogCurrentBodyV15Env,
)

from .simple_dog_current_body_v16_env_cfg import SimpleDogCurrentBodyV16HardEnvCfg


class SimpleDogCurrentBodyV16Env(SimpleDogCurrentBodyV15Env):
    """Increase the graded cost of ignoring a requested body velocity."""

    cfg: SimpleDogCurrentBodyV16HardEnvCfg
