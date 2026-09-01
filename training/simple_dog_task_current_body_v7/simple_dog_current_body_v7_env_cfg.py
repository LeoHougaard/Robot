"""CurrentBodyV7 command-distribution variant; reward mechanics stay V5."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v5.simple_dog_current_body_v5_env_cfg import (
    SimpleDogCurrentBodyV5EvalEnvCfg,
    SimpleDogCurrentBodyV5HardEnvCfg,
    SimpleDogCurrentBodyV5PlayEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV7HardEnvCfg(SimpleDogCurrentBodyV5HardEnvCfg):
    """Nearly all-mixed inputs with long distance-producing holds."""

    policy_family = "current_body_v7"
    mixed_command_fraction = 0.90
    posture_only_fraction = 0.025
    isolated_motion_fraction = 0.05
    neutral_fraction = 0.025
    isolated_linear_axis_fraction = 0.90
    isolated_forward_share = 0.55
    command_hold_s = (4.0, 8.0)
    linear_command_hold_s = (8.0, 12.0)
    command_smoothing_time_s = 0.65


@configclass
class SimpleDogCurrentBodyV7EvalEnvCfg(SimpleDogCurrentBodyV5EvalEnvCfg):
    """Deterministic V7 evaluation."""

    policy_family = "current_body_v7"
    mixed_command_fraction = 0.90
    posture_only_fraction = 0.025
    isolated_motion_fraction = 0.05
    neutral_fraction = 0.025
    isolated_linear_axis_fraction = 0.90
    isolated_forward_share = 0.55
    command_hold_s = (4.0, 8.0)
    linear_command_hold_s = (8.0, 12.0)
    command_smoothing_time_s = 0.65


@configclass
class SimpleDogCurrentBodyV7PlayEnvCfg(SimpleDogCurrentBodyV5PlayEnvCfg):
    """Five-instance V7 visual review."""

    policy_family = "current_body_v7"
    mixed_command_fraction = 0.90
    posture_only_fraction = 0.025
    isolated_motion_fraction = 0.05
    neutral_fraction = 0.025
    isolated_linear_axis_fraction = 0.90
    isolated_forward_share = 0.55
    command_hold_s = (4.0, 8.0)
    linear_command_hold_s = (8.0, 12.0)
    command_smoothing_time_s = 0.65
