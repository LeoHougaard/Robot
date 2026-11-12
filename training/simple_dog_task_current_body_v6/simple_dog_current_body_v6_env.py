"""CurrentBodyV6 environment with persistent translation-heavy inputs."""

from simple_dog_task_current_body_v5.simple_dog_current_body_v5_env import SimpleDogCurrentBodyV5Env

from .simple_dog_current_body_v6_env_cfg import SimpleDogCurrentBodyV6HardEnvCfg


class SimpleDogCurrentBodyV6Env(SimpleDogCurrentBodyV5Env):
    cfg: SimpleDogCurrentBodyV6HardEnvCfg
