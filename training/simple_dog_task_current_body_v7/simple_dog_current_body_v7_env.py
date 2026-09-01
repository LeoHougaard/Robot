"""CurrentBodyV7 environment with longer distance-oriented input holds."""

from simple_dog_task_current_body_v5.simple_dog_current_body_v5_env import SimpleDogCurrentBodyV5Env

from .simple_dog_current_body_v7_env_cfg import SimpleDogCurrentBodyV7HardEnvCfg


class SimpleDogCurrentBodyV7Env(SimpleDogCurrentBodyV5Env):
    cfg: SimpleDogCurrentBodyV7HardEnvCfg
