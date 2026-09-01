"""CurrentBodyV10 translation-bootstrap inputs; rewards stay unchanged."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v7.simple_dog_current_body_v7_env_cfg import (
    SimpleDogCurrentBodyV7EvalEnvCfg,
    SimpleDogCurrentBodyV7HardEnvCfg,
    SimpleDogCurrentBodyV7PlayEnvCfg,
    SimpleDogCurrentBodyV7PushEvalEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV10HardEnvCfg(SimpleDogCurrentBodyV7HardEnvCfg):
    """Bootstrap translation without adding or changing reward terms."""

    policy_family = "current_body_v10"
    mixed_command_fraction = 0.05
    posture_only_fraction = 0.0
    isolated_motion_fraction = 0.90
    neutral_fraction = 0.05
    isolated_linear_axis_fraction = 0.99
    isolated_forward_share = 0.85
    command_hold_s = (6.0, 10.0)
    linear_command_hold_s = (12.0, 16.0)
    command_smoothing_time_s = 0.30


@configclass
class SimpleDogCurrentBodyV10EvalEnvCfg(SimpleDogCurrentBodyV7EvalEnvCfg):
    """Deterministic V10 evaluation."""

    policy_family = "current_body_v10"
    mixed_command_fraction = 0.05
    posture_only_fraction = 0.0
    isolated_motion_fraction = 0.90
    neutral_fraction = 0.05
    isolated_linear_axis_fraction = 0.99
    isolated_forward_share = 0.85
    command_hold_s = (6.0, 10.0)
    linear_command_hold_s = (12.0, 16.0)
    command_smoothing_time_s = 0.30


@configclass
class SimpleDogCurrentBodyV10PlayEnvCfg(SimpleDogCurrentBodyV7PlayEnvCfg):
    """Five-instance V10 visual review."""

    policy_family = "current_body_v10"
    mixed_command_fraction = 0.05
    posture_only_fraction = 0.0
    isolated_motion_fraction = 0.90
    neutral_fraction = 0.05
    isolated_linear_axis_fraction = 0.99
    isolated_forward_share = 0.85
    command_hold_s = (6.0, 10.0)
    linear_command_hold_s = (12.0, 16.0)
    command_smoothing_time_s = 0.30


@configclass
class SimpleDogCurrentBodyV10PushEvalEnvCfg(SimpleDogCurrentBodyV7PushEvalEnvCfg):
    """V10 walking recovery evaluation with repeatable physical pushes."""

    policy_family = "current_body_v10"
    mixed_command_fraction = 0.05
    posture_only_fraction = 0.0
    isolated_motion_fraction = 0.90
    neutral_fraction = 0.05
    isolated_linear_axis_fraction = 0.99
    isolated_forward_share = 0.85
    command_hold_s = (6.0, 10.0)
    linear_command_hold_s = (12.0, 16.0)
    command_smoothing_time_s = 0.30
