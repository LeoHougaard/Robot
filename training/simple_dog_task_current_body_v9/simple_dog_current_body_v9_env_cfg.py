"""CurrentBodyV9 PPO-exploration variant; environment rewards stay unchanged."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v7.simple_dog_current_body_v7_env_cfg import (
    SimpleDogCurrentBodyV7EvalEnvCfg,
    SimpleDogCurrentBodyV7HardEnvCfg,
    SimpleDogCurrentBodyV7PlayEnvCfg,
    SimpleDogCurrentBodyV7PushEvalEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV9HardEnvCfg(SimpleDogCurrentBodyV7HardEnvCfg):
    """Retain V7 inputs and forces while the PPO configuration changes."""

    policy_family = "current_body_v9"


@configclass
class SimpleDogCurrentBodyV9EvalEnvCfg(SimpleDogCurrentBodyV7EvalEnvCfg):
    """Deterministic V9 evaluation."""

    policy_family = "current_body_v9"


@configclass
class SimpleDogCurrentBodyV9PlayEnvCfg(SimpleDogCurrentBodyV7PlayEnvCfg):
    """Five-instance V9 visual review."""

    policy_family = "current_body_v9"


@configclass
class SimpleDogCurrentBodyV9PushEvalEnvCfg(SimpleDogCurrentBodyV7PushEvalEnvCfg):
    """V9 walking recovery evaluation with repeatable physical pushes."""

    policy_family = "current_body_v9"
