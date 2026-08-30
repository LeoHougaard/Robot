"""CurrentV3 rough-locomotion configuration with a deployable 279-value actor."""

from __future__ import annotations

import copy
import os

from isaaclab.utils.configclass import configclass

from current_policy_fit import load_current_policy_fit
from simple_dog_task.simple_dog_env_cfg import CONTROL_PROFILE, JOINT_COUNT
from simple_dog_task_v2.simple_dog_v2_env_cfg import (
    SimpleDogV2RoughEnvCfg,
    SimpleDogV2RoughPlayEnvCfg,
    SimpleDogV2PlayEnvCfg,
)


FIT_PATH = os.environ.get("SIMPLE_DOG_SIMULATION_FIT", "")
if not FIT_PATH:
    raise RuntimeError("CurrentV3 requires SIMPLE_DOG_SIMULATION_FIT")
POLICY_SEMANTICS = tuple(joint["semantic"] for joint in CONTROL_PROFILE["robot"]["joints"])
CURRENT_FIT = load_current_policy_fit(FIT_PATH, POLICY_SEMANTICS, control_hz=50)
_BASE_ROUGH_CFG = SimpleDogV2RoughEnvCfg()
_BASE_PLAY_CFG = SimpleDogV2RoughPlayEnvCfg()
_BASE_FLAT_PLAY_CFG = SimpleDogV2PlayEnvCfg()
_CURRENT_SIM = copy.deepcopy(_BASE_ROUGH_CFG.sim)
_CURRENT_SIM.dt = 1.0 / 200
_CURRENT_SIM.render_interval = 4


MOBILITY_SEGMENTS = (
    ("stand", 100, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    ("forward", 175, 0.18, 0.0, 0.0, 0.0, 0.0, 0.0),
    ("reverse", 175, -0.14, 0.0, 0.0, 0.0, 0.0, 0.0),
    ("strafe_left", 175, 0.0, 0.12, 0.0, 0.0, 0.0, 0.0),
    ("strafe_right", 175, 0.0, -0.12, 0.0, 0.0, 0.0, 0.0),
    ("turn_left", 150, 0.0, 0.0, 0.25, 0.0, 0.0, 0.0),
    ("turn_right", 150, 0.0, 0.0, -0.25, 0.0, 0.0, 0.0),
    ("diagonal_left", 175, 0.16, 0.12, 0.0, 0.0, 0.0, 0.0),
    ("diagonal_right", 175, 0.16, -0.12, 0.0, 0.0, 0.0, 0.0),
    ("diagonal_reverse_left", 175, -0.14, 0.12, 0.0, 0.0, 0.0, 0.0),
    ("diagonal_reverse_right", 175, -0.14, -0.12, 0.0, 0.0, 0.0, 0.0),
    ("curve_left", 175, 0.16, 0.08, 0.25, 0.0, 0.0, 0.0),
    ("curve_right", 175, 0.16, -0.08, -0.25, 0.0, 0.0, 0.0),
    ("stop", 100, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
)

POSTURE_SEGMENTS = (
    ("crouch_walk", 200, 0.14, 0.0, 0.0, -0.030, 0.0, 0.0),
    ("tall_walk", 200, 0.14, 0.0, 0.0, 0.012, 0.0, 0.0),
    ("roll_left_walk", 175, 0.12, 0.0, 0.0, 0.0, 0.10, 0.0),
    ("roll_right_walk", 175, 0.12, 0.0, 0.0, 0.0, -0.10, 0.0),
    ("pitch_up_walk", 175, 0.12, 0.0, 0.0, 0.0, 0.0, 0.10),
    ("pitch_down_walk", 175, 0.12, 0.0, 0.0, 0.0, 0.0, -0.10),
    ("current_dropout_walk", 200, 0.15, 0.0, 0.0, 0.0, 0.0, 0.0),
)


@configclass
class SimpleDogCurrentV3RoughEnvCfg(SimpleDogV2RoughEnvCfg):
    """Balanced current-aware rough locomotion and commanded posture."""

    policy_family = "current_v3"
    observation_history_length = 4
    # The inherited V2 history remains 45 values per frame. CurrentV3 joins
    # its separate 24-value current history immediately before flattening.
    observation_frame_size = 45
    current_observation_frame_size = 45 + 2 * JOINT_COUNT
    posture_command_size = 3
    observation_space = (
        observation_history_length * current_observation_frame_size
        + posture_command_size
    )
    physics_hz = 200
    control_hz = 50
    decimation = 4
    sim = _CURRENT_SIM

    actuator_response_alpha_by_joint = CURRENT_FIT.response_alpha
    actuator_speed_limit_rad_s = CURRENT_FIT.speed_limit_rad_s
    actuator_residual_bias_rad = CURRENT_FIT.residual_bias_rad
    actuator_residual_mad_rad = CURRENT_FIT.residual_mad_rad
    action_delay_steps = CURRENT_FIT.command_delay_steps

    current_bias_ma = CURRENT_FIT.current_bias_ma
    current_scale_ma = CURRENT_FIT.current_scale_ma
    current_clip_ma = CURRENT_FIT.current_clip_ma
    current_noise_mad_ma = CURRENT_FIT.current_noise_mad_ma
    current_delay_steps = CURRENT_FIT.current_delay_steps
    current_dropout_probability_max = 0.03
    current_effort_scale_randomization = (0.65, 1.35)
    force_current_dropout_pattern = False

    posture_height_offset = (-0.035, 0.015)
    posture_roll = (-0.12, 0.12)
    posture_pitch = (-0.12, 0.12)
    posture_hold_s = (2.0, 5.0)
    posture_smoothing_time_s = 0.50
    posture_neutral_fraction = 0.30
    nominal_support_height_m = 0.135
    reset_settle_time_s = 0.0
    reset_hold_randomization_rad = 0.0
    posture_tracking_reward_scale = 1.5
    # Penalize command-relative roll/pitch error directly. The former bounded
    # positive posture average let locomotion reward buy a visibly tilted body.
    # This remains deployable because the actor observes IMU gravity and the
    # requested roll/pitch; simulator attitude is used only by the critic-free
    # reward calculation.
    posture_attitude_error_penalty_scale = -12.0
    posture_height_tracking_std = 0.018
    posture_angle_tracking_std = 0.08

    # Matched flat evaluation at epochs 100 and 125 exposed the same exploit:
    # one foot remained airborne for an entire command while the body stayed
    # nearly stationary. The inherited -1/s maximum cost was too small beside
    # dense locomotion and posture terms. Keep the existing progress-gated
    # stance logic, but make the already independent overlong-air cost strong
    # enough to dominate a parked-foot solution.
    prolonged_foot_air_penalty_scale = -6.0

    # Broad but physically valid CurrentV3 distribution. Friction and
    # restitution use explicit bounds rather than a blanket percentage.
    domain_randomization_enabled = True
    base_mass_scale = (0.75, 1.25)
    base_mass_delta_kg = (0.0, 0.0)
    link_mass_scale = (0.75, 1.25)
    actuator_drive_scale = (0.75, 1.25)
    actuator_effort_scale = (0.75, 1.25)
    actuator_velocity_scale = (0.75, 1.25)
    actuator_response_scale = (0.75, 1.25)
    base_com_range = (0.04, 0.04, 0.035)
    robot_static_friction_range = (0.45, 1.25)
    robot_dynamic_friction_range = (0.35, 1.10)
    robot_restitution_range = (0.0, 0.10)
    material_buckets = 48


@configclass
class SimpleDogCurrentV3CoreEnvCfg(SimpleDogCurrentV3RoughEnvCfg):
    """Learn current-aware flat locomotion before Goal and Rough demands."""

    terrain = copy.deepcopy(_BASE_FLAT_PLAY_CFG.terrain)
    terrain_curriculum = False
    suppress_base_contact_termination = True
    pose_goal_training = False

    # Keep the deployable posture fields in the actor contract, but begin from
    # neutral posture so locomotion cannot be replaced by a static pose reward.
    posture_height_offset = (0.0, 0.0)
    posture_roll = (0.0, 0.0)
    posture_pitch = (0.0, 0.0)
    posture_neutral_fraction = 1.0

    # The final Rough stage retains the broad distribution above. Core uses a
    # measured, moderate distribution while the policy first discovers useful
    # forward/curve motion with the full current and delay observation path.
    base_mass_scale = (0.90, 1.10)
    link_mass_scale = (0.90, 1.10)
    actuator_drive_scale = (0.90, 1.10)
    actuator_effort_scale = (0.85, 1.15)
    actuator_velocity_scale = (0.85, 1.15)
    actuator_response_scale = (0.85, 1.15)
    base_com_range = (0.02, 0.02, 0.015)
    robot_static_friction_range = (0.70, 1.20)
    robot_dynamic_friction_range = (0.60, 1.00)
    robot_restitution_range = (0.0, 0.05)
    material_buckets = 32
    current_dropout_probability_max = 0.01
    current_effort_scale_randomization = (0.85, 1.15)


@configclass
class SimpleDogCurrentV3ReverseEnvCfg(SimpleDogCurrentV3CoreEnvCfg):
    """Add held reverse commands while rehearsing the passing Core gait."""

    pose_goal_training = False
    command_forward = (0.10, 0.22)
    command_lateral = (0.0, 0.0)
    command_yaw = (0.0, 0.0)
    straight_command_fraction = 1.0
    low_speed_straight_fraction = 0.20
    high_speed_straight_fraction = 0.20
    standing_command_fraction = 0.05
    turn_command_fraction = 0.0
    # Symmetric positive/negative exposure supports either continuation from
    # Core or a clean bidirectional foundation. Five percent remains stand.
    reverse_command_fraction = 0.475
    lateral_command_fraction = 0.0
    diagonal_command_fraction = 0.0
    # The cycle-focused continuation activated all four deterministic legs but
    # traded speed away (0.029 forward, -0.020 reverse). Restore a clear margin
    # for signed velocity while retaining the now-proven cycle constraints.
    locomotion_reward_scale = 20.0
    # Deterministic epoch-750 evaluation had real signed reverse progress but
    # kept FR planted while stochastic rollouts still logged four-foot cycles.
    # Reward only a completed set of four landings and shorten the progress-
    # gated maximum stance phase so policy noise cannot supply the missing leg.
    complete_gait_cycle_reward_scale = 20.0
    max_foot_contact_time_s = 0.55
    prolonged_foot_air_penalty_scale = -10.0


@configclass
class SimpleDogCurrentV3LocomotionSpecialistEnvCfg(SimpleDogCurrentV3CoreEnvCfg):
    """Single-axis gait discovery with locomotion far above pose regularization."""

    pose_goal_training = False
    command_forward = (0.16, 0.22)
    command_lateral = (0.0, 0.0)
    command_yaw = (0.0, 0.0)
    straight_command_fraction = 1.0
    low_speed_straight_fraction = 0.25
    high_speed_straight_fraction = 0.25
    standing_command_fraction = 0.05
    turn_command_fraction = 0.0
    lateral_command_fraction = 0.0
    diagonal_command_fraction = 0.0

    # The balanced actor settled near zero velocity because pose stability and
    # gait regularizers remained competitive with directional tracking. These
    # specialists make signed speed the dominant objective. Deterministic
    # landings, slip, fall, height, and contact gates still decide promotion.
    locomotion_reward_scale = 100.0
    minimum_command_speed_fraction = 0.85
    velocity_shortfall_penalty_scale = -240.0
    velocity_tracking_std = 0.08
    posture_tracking_reward_scale = 0.0
    posture_attitude_error_penalty_scale = -100.0
    stability_penalty_scale = -4.0
    # The level-body run held mean tilt to 0.051 rad, but a 0.06 gate cut the
    # useful locomotion gradient by more than half even at that good posture.
    # Keep the direct attitude penalty as the level-body constraint and widen
    # only this gate so the level policy can learn propulsion.
    level_locomotion_gate_std = 0.12
    # Keep the required progress-gated diagonal-pair prior, but remove the
    # overlapping clock, duty, variance, and cycle bonuses that drove reward
    # magnitude up without improving deterministic speed.
    diagonal_gait_reward_scale = 4.0
    complete_gait_cycle_reward_scale = 0.0
    reference_trot_reward_scale = 0.0
    clocked_trot_reward_scale = 0.0
    minimum_swing_duty_fraction = 0.0
    swing_duty_floor_penalty_scale = 0.0
    air_time_variance_penalty_scale = 0.0
    prolonged_foot_air_penalty_scale = -20.0
    max_foot_air_time_s = 0.42
    max_foot_contact_time_s = 0.55
    foot_slip_penalty_scale_v2 = -1.0


@configclass
class SimpleDogCurrentV3ForwardSpecialistEnvCfg(
    SimpleDogCurrentV3LocomotionSpecialistEnvCfg
):
    """Preserve and strengthen the proven forward gait without mode competition."""

    reverse_command_fraction = 0.0


@configclass
class SimpleDogCurrentV3ReverseSpecialistEnvCfg(
    SimpleDogCurrentV3LocomotionSpecialistEnvCfg
):
    """Discover a reverse gait without sacrificing reward to forward rehearsal."""

    reverse_command_fraction = 0.95
    reverse_command_speed = (0.12, 0.18)


@configclass
class SimpleDogCurrentV3StrafeEnvCfg(SimpleDogCurrentV3ReverseEnvCfg):
    """Add pure left/right strafe without removing forward or reverse."""

    reverse_command_fraction = 0.20
    lateral_command_fraction = 0.25


@configclass
class SimpleDogCurrentV3TurnEnvCfg(SimpleDogCurrentV3StrafeEnvCfg):
    """Add pure signed yaw after both translation axes are established."""

    reverse_command_fraction = 0.15
    lateral_command_fraction = 0.20
    turn_command_fraction = 0.20
    command_yaw = (-0.25, 0.25)


@configclass
class SimpleDogCurrentV3GoalEnvCfg(SimpleDogCurrentV3TurnEnvCfg):
    """Complete flat mobility with diagonal and curved commands."""

    reverse_command_fraction = 0.12
    lateral_command_fraction = 0.18
    diagonal_command_fraction = 0.20
    turn_command_fraction = 0.15
    straight_command_fraction = 0.50
    standing_command_fraction = 0.15
    # Mobility is promoted before posture. Hold the deployable posture fields
    # neutral until every planar/yaw direction passes deterministic evaluation.
    posture_height_offset = (0.0, 0.0)
    posture_roll = (0.0, 0.0)
    posture_pitch = (0.0, 0.0)
    posture_neutral_fraction = 1.0
    current_dropout_probability_max = 0.02
    current_effort_scale_randomization = (0.75, 1.25)


@configclass
class SimpleDogCurrentV3PostureEnvCfg(SimpleDogCurrentV3GoalEnvCfg):
    """Add deployable height, roll, and pitch after flat mobility passes."""

    posture_height_offset = (-0.035, 0.015)
    posture_roll = (-0.12, 0.12)
    posture_pitch = (-0.12, 0.12)
    posture_neutral_fraction = 0.35


@configclass
class SimpleDogCurrentV3RoughPolicyEnvCfg(SimpleDogCurrentV3PostureEnvCfg):
    """Move the passing mobility/posture policy onto rough terrain."""

    terrain = copy.deepcopy(_BASE_ROUGH_CFG.terrain)
    terrain_curriculum = _BASE_ROUGH_CFG.terrain_curriculum
    suppress_base_contact_termination = (
        _BASE_ROUGH_CFG.suppress_base_contact_termination
    )
    base_mass_scale = (0.75, 1.25)
    link_mass_scale = (0.75, 1.25)
    actuator_drive_scale = (0.75, 1.25)
    actuator_effort_scale = (0.75, 1.25)
    actuator_velocity_scale = (0.75, 1.25)
    actuator_response_scale = (0.75, 1.25)
    base_com_range = (0.04, 0.04, 0.035)
    robot_static_friction_range = (0.45, 1.25)
    robot_dynamic_friction_range = (0.35, 1.10)
    robot_restitution_range = (0.0, 0.10)
    material_buckets = 48
    current_dropout_probability_max = 0.03
    current_effort_scale_randomization = (0.65, 1.35)


@configclass
class SimpleDogCurrentV3EvalEnvCfg(SimpleDogCurrentV3RoughEnvCfg):
    """Held-out mobility, posture, and deterministic dropout promotion suite."""

    scene = copy.deepcopy(_BASE_PLAY_CFG.scene)
    terrain = copy.deepcopy(_BASE_PLAY_CFG.terrain)
    viewer = copy.deepcopy(_BASE_PLAY_CFG.viewer)
    evaluation_segments = MOBILITY_SEGMENTS + POSTURE_SEGMENTS
    episode_length_s = sum(segment[1] for segment in evaluation_segments) / 50.0 + 2.0
    pose_goal_training = False
    reset_small_tilt_deg = 0.0
    reset_large_tilt_deg = 0.0
    reset_large_tilt_fraction = 0.0
    randomize_reset_yaw = False
    push_probability = 0.0
    observation_noise_enabled = False
    terrain_curriculum = False
    domain_randomization_enabled = False
    current_dropout_probability_max = 0.0
    current_effort_scale_randomization = (1.0, 1.0)
    force_current_dropout_pattern = True
    # V2 currently advances its deterministic segment clock in the play-metric
    # path, so evaluation must keep this enabled even when only EVAL_SEGMENT
    # records are consumed by the gate.
    print_play_metrics = True


@configclass
class SimpleDogCurrentV3PlayEnvCfg(SimpleDogCurrentV3EvalEnvCfg):
    """Single-policy visual review on the held-out CurrentV3 suite."""

    print_play_metrics = True


@configclass
class SimpleDogCurrentV3FlatEvalEnvCfg(SimpleDogCurrentV3EvalEnvCfg):
    """Matched full-suite regression screen on a plane."""

    terrain = copy.deepcopy(_BASE_FLAT_PLAY_CFG.terrain)


@configclass
class SimpleDogCurrentV3StressEvalEnvCfg(SimpleDogCurrentV3EvalEnvCfg):
    """Fixed-seed broad robot/current randomization with repeated pushes."""

    domain_randomization_enabled = True
    observation_noise_enabled = True
    push_interval_s = (2.0, 2.0)
    push_probability = 1.0
    push_linear_velocity = 0.20
    push_yaw_velocity = 0.15
    current_dropout_probability_max = 0.03
    current_effort_scale_randomization = (0.65, 1.35)
