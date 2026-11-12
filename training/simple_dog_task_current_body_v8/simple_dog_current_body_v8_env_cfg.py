"""CurrentBodyV8 isolated-translation input variant; rewards stay unchanged."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v7.simple_dog_current_body_v7_env_cfg import (
    SimpleDogCurrentBodyV7EvalEnvCfg,
    SimpleDogCurrentBodyV7HardEnvCfg,
    SimpleDogCurrentBodyV7PlayEnvCfg,
    SimpleDogCurrentBodyV7PushEvalEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV8HardEnvCfg(SimpleDogCurrentBodyV7HardEnvCfg):
    """Spend much more input time on single-axis translational discovery."""

    policy_family = "current_body_v8"
    mixed_command_fraction = 0.60
    posture_only_fraction = 0.025
    isolated_motion_fraction = 0.35
    neutral_fraction = 0.025
    isolated_linear_axis_fraction = 0.98
    isolated_forward_share = 0.65
    command_hold_s = (4.0, 8.0)
    linear_command_hold_s = (10.0, 14.0)
    command_smoothing_time_s = 0.40


@configclass
class SimpleDogCurrentBodyV8EvalEnvCfg(SimpleDogCurrentBodyV7EvalEnvCfg):
    """Deterministic V8 evaluation."""

    policy_family = "current_body_v8"
    mixed_command_fraction = 0.60
    posture_only_fraction = 0.025
    isolated_motion_fraction = 0.35
    neutral_fraction = 0.025
    isolated_linear_axis_fraction = 0.98
    isolated_forward_share = 0.65
    command_hold_s = (4.0, 8.0)
    linear_command_hold_s = (10.0, 14.0)
    command_smoothing_time_s = 0.40


@configclass
class SimpleDogCurrentBodyV8PlayEnvCfg(SimpleDogCurrentBodyV7PlayEnvCfg):
    """Five-instance V8 visual review."""

    policy_family = "current_body_v8"
    mixed_command_fraction = 0.60
    posture_only_fraction = 0.025
    isolated_motion_fraction = 0.35
    neutral_fraction = 0.025
    isolated_linear_axis_fraction = 0.98
    isolated_forward_share = 0.65
    command_hold_s = (4.0, 8.0)
    linear_command_hold_s = (10.0, 14.0)
    command_smoothing_time_s = 0.40


@configclass
class SimpleDogCurrentBodyV8PushEvalEnvCfg(SimpleDogCurrentBodyV7PushEvalEnvCfg):
    """V8 walking recovery evaluation with repeatable physical pushes."""

    policy_family = "current_body_v8"
    mixed_command_fraction = 0.60
    posture_only_fraction = 0.025
    isolated_motion_fraction = 0.35
    neutral_fraction = 0.025
    isolated_linear_axis_fraction = 0.98
    isolated_forward_share = 0.65
    command_hold_s = (4.0, 8.0)
    linear_command_hold_s = (10.0, 14.0)
    command_smoothing_time_s = 0.40
