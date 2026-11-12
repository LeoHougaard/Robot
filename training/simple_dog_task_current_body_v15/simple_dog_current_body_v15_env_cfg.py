"""CurrentBodyV15 delayed-push curriculum; observations and rewards stay V14."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v14.simple_dog_current_body_v14_env_cfg import (
    SimpleDogCurrentBodyV14EvalEnvCfg,
    SimpleDogCurrentBodyV14HardEnvCfg,
    SimpleDogCurrentBodyV14PlayEnvCfg,
    SimpleDogCurrentBodyV14PushEvalEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV15HardEnvCfg(SimpleDogCurrentBodyV14HardEnvCfg):
    """Ramp physical recovery forces from terrain difficulty instead of 60%."""

    policy_family = "current_body_v15"
    push_difficulty_floor = 0.0


@configclass
class SimpleDogCurrentBodyV15EvalEnvCfg(SimpleDogCurrentBodyV14EvalEnvCfg):
    policy_family = "current_body_v15"


@configclass
class SimpleDogCurrentBodyV15PlayEnvCfg(SimpleDogCurrentBodyV14PlayEnvCfg):
    policy_family = "current_body_v15"


@configclass
class SimpleDogCurrentBodyV15PushEvalEnvCfg(SimpleDogCurrentBodyV14PushEvalEnvCfg):
    policy_family = "current_body_v15"
