"""Explicit initial delivery distribution: ordinary surfaces and slow commands.

Reuse the asset/reset/current-sensor machinery in V4; do not inherit the
subsequent reward experiments. Wider terrain requires a separately evaluated
continuation. Nothing here automatically advances difficulty with wall time.
"""
import copy
import json
from pathlib import Path

import isaaclab.terrains as terrain_gen
from isaaclab.sensors import RayCasterCfg, patterns
from isaaclab.terrains import TerrainGeneratorCfg
from isaaclab.utils.configclass import configclass
from simple_dog_task_current_body_v4.simple_dog_current_body_v4_env_cfg import SimpleDogCurrentBodyV4HardEnvCfg
from delivery_contract import SEGMENTS, HISTORY_INDICES

SERVO_FIT_PATH = Path(__file__).resolve().parents[1] / "fits/servo-response-20260829.json"
SERVO_FIT = json.loads(SERVO_FIT_PATH.read_text())

_terrain = copy.deepcopy(SimpleDogCurrentBodyV4HardEnvCfg().terrain)
_terrain.max_init_terrain_level = 0
_terrain.terrain_generator = TerrainGeneratorCfg(
    seed=5020, curriculum=True, size=(4., 4.), border_width=1.,
    num_rows=2, num_cols=8, horizontal_scale=.05, vertical_scale=.001,
    slope_threshold=.75, difficulty_range=(0., 1.), use_cache=False,
    sub_terrains={
        "floor": terrain_gen.MeshPlaneTerrainCfg(proportion=.50),
        "small_uneven": terrain_gen.HfRandomUniformTerrainCfg(
            proportion=.25, noise_range=(.001, .006), noise_step=.001, border_width=.2),
        "gentle_up": terrain_gen.HfPyramidSlopedTerrainCfg(
            proportion=.125, slope_range=(.01, .06), platform_width=.8, border_width=.2),
        "gentle_down": terrain_gen.HfInvertedPyramidSlopedTerrainCfg(
            proportion=.125, slope_range=(.01, .06), platform_width=.8, border_width=.2),
    },
)


@configclass
class DeliveryTrainCfg(SimpleDogCurrentBodyV4HardEnvCfg):
    policy_family = "current_body_v20"
    # Last four frames are consecutive, allowing exact initialization from
    # the recorded V2 actor while retaining longer context and current input.
    selected_history_indices = HISTORY_INDICES
    observation_space = 426
    # Training-only value network sees ground-truth velocity/posture/contact.
    # The exported actor still receives the same 426 physical sensor values.
    state_space = 436
    # Reward/evaluation only. The actor still receives exactly 426 physical
    # sensor/history/command values. Measure vertical clearance above terrain
    # beneath the body, not above the moving lower-leg centers of mass.
    nominal_support_height_m = .18
    height_scanner = RayCasterCfg(
        prim_path=(SimpleDogCurrentBodyV4HardEnvCfg().robot.prim_path + "/links/"
                   + SimpleDogCurrentBodyV4HardEnvCfg().base_contact_pattern),
        update_period=.02,
        ray_alignment="yaw",
        pattern_cfg=patterns.GridPatternCfg(resolution=.075, size=(.30, .30)),
        mesh_prim_paths=["/World/ground"],
        max_distance=2.,
        debug_vis=False,
    )
    episode_length_s = 12.
    terrain = _terrain
    terrain_curriculum = False
    difficulty_ramp_floor = 1.
    difficulty_ramp_full_step = 1
    reset_spawn_clearance_m = .015
    reset_settle_time_s = 1.
    reset_hold_randomization_rad = 0.
    reset_small_tilt_deg = 2.
    reset_large_tilt_deg = 5.
    reset_large_tilt_fraction = .1
    reset_joint_position_noise = .015
    reset_joint_velocity_noise = .05
    termination_height = .08
    suppress_base_contact_termination = True
    command_hold_s = (3., 6.)
    linear_command_hold_s = command_hold_s
    command_smoothing_time_s = .4
    posture_smoothing_time_s = .5
    mixed_command_fraction = .20
    posture_only_fraction = .05
    isolated_motion_fraction = .65
    neutral_fraction = .10
    isolated_linear_axis_fraction = .80
    isolated_forward_share = .85
    command_forward_v4 = (-.10, .10)
    command_lateral_v4 = (-.08, .08)
    command_yaw_v4 = (-.30, .30)
    posture_height_offset = (-.012, .012)
    posture_roll = (-.06, .06)
    posture_pitch = (-.06, .06)
    capability_tail_fraction = 0.
    # Active stabilization at zero motion command is part of this new action
    # contract. Old exports keep their original stationary override.
    stationary_planar_deadband = -1.
    stationary_yaw_deadband = -1.
    actuator_response_alpha_by_joint = (1.,) * 12
    actuator_response_scale = (1., 1.)
    actuator_speed_limit_rad_s = (100.,) * 12
    actuator_residual_bias_rad = (0.,) * 12
    actuator_residual_mad_rad = (0.,) * 12
    action_delay_steps = (0, 0)
    timing_interval_ms = (20., 20.)
    base_mass_scale = (.90, 1.10)
    base_mass_delta_kg = (0., .05)
    link_mass_scale = (.90, 1.10)
    independent_inertia_scale = (.90, 1.10)
    base_com_offset_semantic = (0., 0., 0.)
    base_com_range = (.012, .012, .008)
    actuator_drive_scale = (.85, 1.15)
    actuator_effort_scale = (.85, 1.10)
    actuator_velocity_scale = (1., 1.)
    robot_static_friction_range = (.65, 1.20)
    robot_dynamic_friction_range = (.50, 1.05)
    robot_restitution_range = (0., .03)
    current_dropout_probability_max = .02
    current_effort_scale_randomization = (.75, 1.25)
    push_probability = 0.
    gyro_noise = .03
    joint_position_noise = .0015
    accelerometer_noise_mg = 10.
    opposite_leg_sync_reward_scale = .25


@configclass
class DeliveryEvalCfg(DeliveryTrainCfg):
    # Initial delivery envelope, fixed before PPO. Compare the initialized
    # actor and every candidate under this same screen and source snapshot.
    evaluation_segments = SEGMENTS
    # A one-robot evaluation of the training mixture selects its first terrain
    # column (a plane). Use an explicit held-out uneven surface so the mild
    # suite cannot silently repeat the flat suite.
    terrain = copy.deepcopy(_terrain)
    terrain.terrain_generator = TerrainGeneratorCfg(
        seed=7042, curriculum=False, size=(6., 6.), border_width=1.,
        num_rows=1, num_cols=1, horizontal_scale=.05, vertical_scale=.001,
        slope_threshold=.75, difficulty_range=(1., 1.), use_cache=False,
        sub_terrains={"small_uneven": terrain_gen.HfRandomUniformTerrainCfg(
            proportion=1., noise_range=(.001, .006), noise_step=.001, border_width=.2)},
    )
    video_recorder = copy.deepcopy(DeliveryTrainCfg().video_recorder)
    video_recorder.window_width = 640
    video_recorder.window_height = 360
    episode_length_s = sum(s[1] for s in evaluation_segments) / 50 + 10.
    domain_randomization_enabled = False
    observation_noise_enabled = False
    current_dropout_probability_max = 0.
    current_effort_scale_randomization = (1., 1.)
    reset_small_tilt_deg = 0.
    reset_large_tilt_deg = 0.
    reset_large_tilt_fraction = 0.
    randomize_reset_yaw = False
    reset_joint_position_noise = 0.
    reset_joint_velocity_noise = 0.


@configclass
class DeliveryFlatEvalCfg(DeliveryEvalCfg):
    terrain = copy.deepcopy(_terrain)
    terrain.terrain_type = "plane"
    terrain.terrain_generator = None


@configclass
class DeliveryStressEvalCfg(DeliveryEvalCfg):
    domain_randomization_enabled = True
    observation_noise_enabled = True
    current_effort_scale_randomization = (.75, 1.25)
    push_probability = 1.
    push_interval_s = (6., 6.)
    push_linear_velocity = .08
    push_yaw_velocity = .08
