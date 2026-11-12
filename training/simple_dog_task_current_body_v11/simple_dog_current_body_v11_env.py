"""CurrentBodyV11 environment with staged terrain and physical pushes."""

from simple_dog_task_current_body_v10.simple_dog_current_body_v10_env import (
    SimpleDogCurrentBodyV10Env,
)

from .simple_dog_current_body_v11_env_cfg import SimpleDogCurrentBodyV11HardEnvCfg


class SimpleDogCurrentBodyV11Env(SimpleDogCurrentBodyV10Env):
    """Reuse V10 inputs and unchanged rewards with an easier discovery stage."""

    cfg: SimpleDogCurrentBodyV11HardEnvCfg
