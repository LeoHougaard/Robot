"""CurrentBodyV10 environment with translation-bootstrap inputs and pushes."""

from simple_dog_task_current_body_v7.simple_dog_current_body_v7_env import (
    SimpleDogCurrentBodyV7Env,
)

from .simple_dog_current_body_v10_env_cfg import SimpleDogCurrentBodyV10HardEnvCfg


class SimpleDogCurrentBodyV10Env(SimpleDogCurrentBodyV7Env):
    """Reuse V7 physics and rewards while changing command inputs only."""

    cfg: SimpleDogCurrentBodyV10HardEnvCfg
