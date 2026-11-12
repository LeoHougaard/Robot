"""CurrentBodyV19 with the V18 objective and a stiff actuator training range."""

from __future__ import annotations

from simple_dog_task_current_body_v18.simple_dog_current_body_v18_env import (
    SimpleDogCurrentBodyV18Env,
)

from .simple_dog_current_body_v19_env_cfg import SimpleDogCurrentBodyV19HardEnvCfg


class SimpleDogCurrentBodyV19Env(SimpleDogCurrentBodyV18Env):
    """V18 locomotion objective trained without weak-drive domain samples."""

    cfg: SimpleDogCurrentBodyV19HardEnvCfg
