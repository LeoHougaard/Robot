"""CurrentBodyV13 long-horizon and early-push exploration; rewards unchanged."""

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v12.simple_dog_current_body_v12_env_cfg import (
    SimpleDogCurrentBodyV12EvalEnvCfg,
    SimpleDogCurrentBodyV12HardEnvCfg,
    SimpleDogCurrentBodyV12PlayEnvCfg,
    SimpleDogCurrentBodyV12PushEvalEnvCfg,
)


@configclass
class SimpleDogCurrentBodyV13HardEnvCfg(SimpleDogCurrentBodyV12HardEnvCfg):
    """Hold one translation command and perturb balance without reward priors."""

    policy_family = "current_body_v13"
    mixed_command_fraction = 0.0
    posture_only_fraction = 0.0
    isolated_motion_fraction = 1.0
    neutral_fraction = 0.0
    isolated_linear_axis_fraction = 1.0
    isolated_forward_share = 0.80
    command_hold_s = (14.0, 18.0)
    linear_command_hold_s = (14.0, 18.0)
    command_smoothing_time_s = 0.15

    # V11 intentionally begins at 1% terrain difficulty. Decouple the body
    # wrench curriculum so early policies still experience recoverable balance
    # errors that can expose foot-lifting actions. These remain physical,
    # random off-centre forces and are never used as reward or velocity edits.
    push_difficulty_floor = 0.60
    push_probability = 0.75
    push_interval_s = (3.0, 6.0)
    push_force_duration_s = (0.08, 0.14)
    push_force_n = (4.0, 10.0)
    push_yaw_torque_nm = 0.35
    push_application_offset_m = (0.08, 0.05, 0.04)


@configclass
class SimpleDogCurrentBodyV13EvalEnvCfg(SimpleDogCurrentBodyV12EvalEnvCfg):
    policy_family = "current_body_v13"


@configclass
class SimpleDogCurrentBodyV13PlayEnvCfg(SimpleDogCurrentBodyV12PlayEnvCfg):
    policy_family = "current_body_v13"


@configclass
class SimpleDogCurrentBodyV13PushEvalEnvCfg(SimpleDogCurrentBodyV12PushEvalEnvCfg):
    policy_family = "current_body_v13"
