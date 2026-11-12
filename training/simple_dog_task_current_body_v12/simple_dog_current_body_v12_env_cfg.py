"""CurrentBodyV12 PPO-exploration variant; environment rewards stay unchanged."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v11.simple_dog_current_body_v11_env_cfg import (
    SimpleDogCurrentBodyV11EvalEnvCfg,
    SimpleDogCurrentBodyV11HardEnvCfg,
    SimpleDogCurrentBodyV11PlayEnvCfg,
    SimpleDogCurrentBodyV11PushEvalEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV12HardEnvCfg(SimpleDogCurrentBodyV11HardEnvCfg):
    policy_family = "current_body_v12"


@configclass
class SimpleDogCurrentBodyV12EvalEnvCfg(SimpleDogCurrentBodyV11EvalEnvCfg):
    policy_family = "current_body_v12"


@configclass
class SimpleDogCurrentBodyV12PlayEnvCfg(SimpleDogCurrentBodyV11PlayEnvCfg):
    policy_family = "current_body_v12"


@configclass
class SimpleDogCurrentBodyV12PushEvalEnvCfg(SimpleDogCurrentBodyV11PushEvalEnvCfg):
    policy_family = "current_body_v12"
