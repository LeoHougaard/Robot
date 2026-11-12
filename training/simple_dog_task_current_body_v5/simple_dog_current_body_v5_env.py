"""CurrentBodyV5 environment with no prescribed leg pairing."""

from simple_dog_task_current_body_v4.simple_dog_current_body_v4_env import (
    SimpleDogCurrentBodyV4Env,
)

from .simple_dog_current_body_v5_env_cfg import SimpleDogCurrentBodyV5HardEnvCfg


class SimpleDogCurrentBodyV5Env(SimpleDogCurrentBodyV4Env):
    """Versioned V5 namespace for the no-pairing body-control mechanics."""

    cfg: SimpleDogCurrentBodyV5HardEnvCfg
