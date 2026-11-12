"""CurrentBodyV6 command-distribution variant; reward mechanics stay V5."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v5.simple_dog_current_body_v5_env_cfg import (
    SimpleDogCurrentBodyV5EvalEnvCfg,
    SimpleDogCurrentBodyV5HardEnvCfg,
    SimpleDogCurrentBodyV5PlayEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV6HardEnvCfg(SimpleDogCurrentBodyV5HardEnvCfg):
    """Translation-heavy mixed inputs with medium persistent holds."""

    policy_family = "current_body_v6"
    mixed_command_fraction = 0.85
    posture_only_fraction = 0.025
    isolated_motion_fraction = 0.10
    neutral_fraction = 0.025
    isolated_linear_axis_fraction = 0.95
    isolated_forward_share = 0.70
    command_hold_s = (3.0, 6.0)
    linear_command_hold_s = (6.0, 10.0)
    command_smoothing_time_s = 0.50


@configclass
class SimpleDogCurrentBodyV6EvalEnvCfg(SimpleDogCurrentBodyV5EvalEnvCfg):
    """Deterministic V6 evaluation."""

    policy_family = "current_body_v6"
    mixed_command_fraction = 0.85
    posture_only_fraction = 0.025
    isolated_motion_fraction = 0.10
    neutral_fraction = 0.025
    isolated_linear_axis_fraction = 0.95
    isolated_forward_share = 0.70
    command_hold_s = (3.0, 6.0)
    linear_command_hold_s = (6.0, 10.0)
    command_smoothing_time_s = 0.50


@configclass
class SimpleDogCurrentBodyV6PlayEnvCfg(SimpleDogCurrentBodyV5PlayEnvCfg):
    """Five-instance V6 visual review."""

    policy_family = "current_body_v6"
    mixed_command_fraction = 0.85
    posture_only_fraction = 0.025
    isolated_motion_fraction = 0.10
    neutral_fraction = 0.025
    isolated_linear_axis_fraction = 0.95
    isolated_forward_share = 0.70
    command_hold_s = (3.0, 6.0)
    linear_command_hold_s = (6.0, 10.0)
    command_smoothing_time_s = 0.50
