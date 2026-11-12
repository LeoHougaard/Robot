"""CurrentBodyV7 distance-and-push variant; reward mechanics stay V5."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v5.simple_dog_current_body_v5_env_cfg import (
    SimpleDogCurrentBodyV5EvalEnvCfg,
    SimpleDogCurrentBodyV5HardEnvCfg,
    SimpleDogCurrentBodyV5PlayEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV7HardEnvCfg(SimpleDogCurrentBodyV5HardEnvCfg):
    """Long distance-producing holds with brief, recoverable body pushes."""

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

    # Leave long quiet windows for locomotion discovery, then apply a short
    # off-centre chassis force.  These are physical forces (N/Nm), not reward
    # terms or direct root-velocity edits.  The inherited V4 difficulty ramp
    # scales both event probability and magnitude from mild to full strength.
    push_probability = 0.35
    push_difficulty_floor = 0.0
    push_interval_s = (6.0, 10.0)
    push_force_duration_s = (0.08, 0.16)
    push_force_n = (4.0, 12.0)
    push_yaw_torque_nm = 0.50
    push_application_offset_m = (0.10, 0.06, 0.05)


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
    # The V7 environment reads this field for every registered mode. Normal
    # deterministic evaluation does not apply early curriculum pushes.
    push_difficulty_floor = 0.0


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
    # Visual review uses the same environment class as training, so keep its
    # push-curriculum contract complete while leaving the floor disabled.
    push_difficulty_floor = 0.0


@configclass
class SimpleDogCurrentBodyV7PushEvalEnvCfg(SimpleDogCurrentBodyV7EvalEnvCfg):
    """Repeatable three-second body pushes for the recovery acceptance gate."""

    push_probability = 1.0
    push_interval_s = (3.0, 3.0)
    push_force_duration_s = (0.12, 0.12)
    push_force_n = (8.0, 8.0)
    push_yaw_torque_nm = 0.35
    push_application_offset_m = (0.08, 0.05, 0.04)
    difficulty_ramp_floor = 1.0
    difficulty_ramp_full_step = 1
