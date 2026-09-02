"""CurrentBodyV13 environment retaining V12 observations and rewards."""

from simple_dog_task_current_body_v12.simple_dog_current_body_v12_env import (
    SimpleDogCurrentBodyV12Env,
)

from .simple_dog_current_body_v13_env_cfg import SimpleDogCurrentBodyV13HardEnvCfg


class SimpleDogCurrentBodyV13Env(SimpleDogCurrentBodyV12Env):
    """Test longer temporal credit and useful early physical disturbances."""

    cfg: SimpleDogCurrentBodyV13HardEnvCfg
