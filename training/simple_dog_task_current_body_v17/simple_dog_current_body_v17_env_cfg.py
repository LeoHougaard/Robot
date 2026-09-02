"""CurrentBodyV17 locomotion-only commands and reward."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v16.simple_dog_current_body_v16_env_cfg import (
    SimpleDogCurrentBodyV16EvalEnvCfg,
    SimpleDogCurrentBodyV16HardEnvCfg,
    SimpleDogCurrentBodyV16PlayEnvCfg,
    SimpleDogCurrentBodyV16PushEvalEnvCfg,
)


V17_EVALUATION_SEGMENTS = (
    ("stand", 100, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    ("forward", 200, 0.15, 0.0, 0.0, 0.0, 0.0, 0.0),
    ("reverse", 200, -0.15, 0.0, 0.0, 0.0, 0.0, 0.0),
    ("strafe_left", 200, 0.0, 0.12, 0.0, 0.0, 0.0, 0.0),
    ("strafe_right", 200, 0.0, -0.12, 0.0, 0.0, 0.0, 0.0),
    ("turn_left", 200, 0.0, 0.0, 0.25, 0.0, 0.0, 0.0),
    ("turn_right", 200, 0.0, 0.0, -0.25, 0.0, 0.0, 0.0),
    ("diagonal_left", 200, 0.12, 0.10, 0.0, 0.0, 0.0, 0.0),
    ("diagonal_right", 200, 0.12, -0.10, 0.0, 0.0, 0.0, 0.0),
    ("diagonal_reverse_left", 200, -0.12, 0.10, 0.0, 0.0, 0.0, 0.0),
    ("diagonal_reverse_right", 200, -0.12, -0.10, 0.0, 0.0, 0.0, 0.0),
    ("curve_left", 200, 0.15, 0.0, 0.20, 0.0, 0.0, 0.0),
    ("curve_right", 200, 0.15, 0.0, -0.20, 0.0, 0.0, 0.0),
    ("stop", 100, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
)
V17_EVALUATION_EPISODE_SECONDS = (
    sum(segment[1] for segment in V17_EVALUATION_SEGMENTS) / 50.0 + 2.0
)


class _V17LocomotionObjective:
    policy_family = "current_body_v17"
    episode_length_s = 90.0
    locomotion_translation_fraction = 0.90
    locomotion_forward_fraction = 0.55
    locomotion_lateral_fraction = 0.15
    locomotion_mixed_fraction = 0.20
    locomotion_yaw_only_fraction = 0.10
    locomotion_speed_range = (0.10, 0.20)
    locomotion_lateral_speed_range = (0.10, 0.16)
    locomotion_yaw_rate_range = (0.12, 0.35)
    linear_command_hold_s = (4.0, 7.0)
    command_hold_s = (4.0, 7.0)
    command_smoothing_time_s = 0.25
    locomotion_tracking_reward_scale = 2.0
    locomotion_progress_reward_scale = 3.0
    locomotion_shortfall_penalty_scale = -6.0
    locomotion_level_penalty_scale = -2.0
    locomotion_level_tolerance_rad = 0.12


@configclass
class SimpleDogCurrentBodyV17HardEnvCfg(
    _V17LocomotionObjective, SimpleDogCurrentBodyV16HardEnvCfg
):
    pass


@configclass
class SimpleDogCurrentBodyV17EvalEnvCfg(
    _V17LocomotionObjective, SimpleDogCurrentBodyV16EvalEnvCfg
):
    evaluation_segments = V17_EVALUATION_SEGMENTS
    episode_length_s = V17_EVALUATION_EPISODE_SECONDS


@configclass
class SimpleDogCurrentBodyV17PlayEnvCfg(
    _V17LocomotionObjective, SimpleDogCurrentBodyV16PlayEnvCfg
):
    evaluation_segments = V17_EVALUATION_SEGMENTS
    episode_length_s = V17_EVALUATION_EPISODE_SECONDS


@configclass
class SimpleDogCurrentBodyV17PushEvalEnvCfg(
    _V17LocomotionObjective, SimpleDogCurrentBodyV16PushEvalEnvCfg
):
    evaluation_segments = V17_EVALUATION_SEGMENTS
    episode_length_s = V17_EVALUATION_EPISODE_SECONDS
