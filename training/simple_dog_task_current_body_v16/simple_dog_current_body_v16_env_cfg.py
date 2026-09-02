"""CurrentBodyV16 strong no-motion penalty; inputs and physics stay V15."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v15.simple_dog_current_body_v15_env_cfg import (
    SimpleDogCurrentBodyV15EvalEnvCfg,
    SimpleDogCurrentBodyV15HardEnvCfg,
    SimpleDogCurrentBodyV15PlayEnvCfg,
    SimpleDogCurrentBodyV15PushEvalEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV16HardEnvCfg(SimpleDogCurrentBodyV15HardEnvCfg):
    policy_family = "current_body_v16"
    body_motion_shortfall_penalty_scale = -3.0


@configclass
class SimpleDogCurrentBodyV16EvalEnvCfg(SimpleDogCurrentBodyV15EvalEnvCfg):
    policy_family = "current_body_v16"
    body_motion_shortfall_penalty_scale = -3.0


@configclass
class SimpleDogCurrentBodyV16PlayEnvCfg(SimpleDogCurrentBodyV15PlayEnvCfg):
    policy_family = "current_body_v16"
    body_motion_shortfall_penalty_scale = -3.0


@configclass
class SimpleDogCurrentBodyV16PushEvalEnvCfg(SimpleDogCurrentBodyV15PushEvalEnvCfg):
    policy_family = "current_body_v16"
    body_motion_shortfall_penalty_scale = -3.0
