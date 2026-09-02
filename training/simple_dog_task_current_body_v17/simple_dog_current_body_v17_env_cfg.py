"""CurrentBodyV17 locomotion-only commands and reward."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v16.simple_dog_current_body_v16_env_cfg import (
    SimpleDogCurrentBodyV16EvalEnvCfg,
    SimpleDogCurrentBodyV16HardEnvCfg,
    SimpleDogCurrentBodyV16PlayEnvCfg,
    SimpleDogCurrentBodyV16PushEvalEnvCfg,
)


class _V17LocomotionObjective:
    policy_family = "current_body_v17"
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
    pass


@configclass
class SimpleDogCurrentBodyV17PlayEnvCfg(
    _V17LocomotionObjective, SimpleDogCurrentBodyV16PlayEnvCfg
):
    pass


@configclass
class SimpleDogCurrentBodyV17PushEvalEnvCfg(
    _V17LocomotionObjective, SimpleDogCurrentBodyV16PushEvalEnvCfg
):
    pass
