"""CurrentBodyV5 no-pairing scratch training configuration."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v4.simple_dog_current_body_v4_env_cfg import (
    SimpleDogCurrentBodyV4EvalEnvCfg,
    SimpleDogCurrentBodyV4HardEnvCfg,
    SimpleDogCurrentBodyV4PlayEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV5HardEnvCfg(SimpleDogCurrentBodyV4HardEnvCfg):
    """Fresh V5 policy family with every inherited gait-pair term disabled."""

    policy_family = "current_body_v5"
    gait_reward_scale = 0.0
    diagonal_gait_reward_scale = 0.0
    complete_gait_cycle_reward_scale = 0.0
    reference_trot_reward_scale = 0.0
    clocked_trot_reward_scale = 0.0
    diagonal_joint_symmetry_reward_scale = 0.0


@configclass
class SimpleDogCurrentBodyV5EvalEnvCfg(SimpleDogCurrentBodyV4EvalEnvCfg):
    """Deterministic V5 evaluation under the unchanged held-out commands."""

    policy_family = "current_body_v5"


@configclass
class SimpleDogCurrentBodyV5PlayEnvCfg(SimpleDogCurrentBodyV4PlayEnvCfg):
    """Five-instance V5 visual review."""

    policy_family = "current_body_v5"
