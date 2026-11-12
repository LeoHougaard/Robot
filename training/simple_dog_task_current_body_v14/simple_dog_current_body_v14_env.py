"""CurrentBodyV14 environment retaining V13 physics and rewards."""

from simple_dog_task_current_body_v13.simple_dog_current_body_v13_env import (
    SimpleDogCurrentBodyV13Env,
)

from .simple_dog_current_body_v14_env_cfg import SimpleDogCurrentBodyV14HardEnvCfg


class SimpleDogCurrentBodyV14Env(SimpleDogCurrentBodyV13Env):
    """Test whether a compact deployable history improves locomotion learning."""

    cfg: SimpleDogCurrentBodyV14HardEnvCfg
