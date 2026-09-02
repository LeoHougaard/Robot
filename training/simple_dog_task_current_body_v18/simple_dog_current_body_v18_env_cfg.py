"""CurrentBodyV18 adds a small progress-gated opposite-leg sync term."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v17.simple_dog_current_body_v17_env_cfg import (
    SimpleDogCurrentBodyV17EvalEnvCfg,
    SimpleDogCurrentBodyV17HardEnvCfg,
    SimpleDogCurrentBodyV17PlayEnvCfg,
    SimpleDogCurrentBodyV17PushEvalEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV18HardEnvCfg(SimpleDogCurrentBodyV17HardEnvCfg):
    policy_family = "current_body_v18"
    opposite_leg_sync_reward_scale = 0.25


@configclass
class SimpleDogCurrentBodyV18EvalEnvCfg(SimpleDogCurrentBodyV17EvalEnvCfg):
    policy_family = "current_body_v18"
    opposite_leg_sync_reward_scale = 0.25


@configclass
class SimpleDogCurrentBodyV18PlayEnvCfg(SimpleDogCurrentBodyV17PlayEnvCfg):
    policy_family = "current_body_v18"
    opposite_leg_sync_reward_scale = 0.25


@configclass
class SimpleDogCurrentBodyV18PushEvalEnvCfg(SimpleDogCurrentBodyV17PushEvalEnvCfg):
    policy_family = "current_body_v18"
    opposite_leg_sync_reward_scale = 0.25
