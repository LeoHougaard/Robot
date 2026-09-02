"""CurrentBodyV19 biases actuator randomization toward nominal-to-stiff response."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v18.simple_dog_current_body_v18_env_cfg import (
    SimpleDogCurrentBodyV18EvalEnvCfg,
    SimpleDogCurrentBodyV18HardEnvCfg,
    SimpleDogCurrentBodyV18PlayEnvCfg,
    SimpleDogCurrentBodyV18PushEvalEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV19HardEnvCfg(SimpleDogCurrentBodyV18HardEnvCfg):
    policy_family = "current_body_v19"
    # V4-V18 sample 0.65-1.35. V19 keeps the same upper bound but removes
    # weak-drive samples, isolating the user's requested stiff alternative.
    actuator_drive_scale = (1.0, 1.35)


@configclass
class SimpleDogCurrentBodyV19EvalEnvCfg(SimpleDogCurrentBodyV18EvalEnvCfg):
    policy_family = "current_body_v19"


@configclass
class SimpleDogCurrentBodyV19PlayEnvCfg(SimpleDogCurrentBodyV18PlayEnvCfg):
    policy_family = "current_body_v19"


@configclass
class SimpleDogCurrentBodyV19PushEvalEnvCfg(SimpleDogCurrentBodyV18PushEvalEnvCfg):
    policy_family = "current_body_v19"
