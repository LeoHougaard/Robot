"""CurrentBodyV4 full-hard, six-command, current-aware training configuration."""

from __future__ import annotations

import copy
import math
import os

import isaaclab.sim as sim_utils
import isaaclab.terrains as terrain_gen
from isaaclab.envs.common import ViewerCfg
from isaaclab.terrains import TerrainGeneratorCfg, TerrainImporterCfg
from isaaclab.utils.configclass import configclass

from simple_dog_task.simple_dog_env_cfg import CONTROL_PROFILE, JOINT_COUNT
from simple_dog_task_current.simple_dog_current_env_cfg import (
    CURRENT_FIT,
    SimpleDogCurrentV3RoughEnvCfg,
)


V4_HISTORY_FRAMES = 24
V4_SELECTED_HISTORY_INDICES = (0, 3, 6, 9, 12, 15, 18, 19, 20, 21, 22, 23)
V4_FRAME_SIZE = 45 + 2 * JOINT_COUNT + 1
V4_COMMAND_SIZE = 6
V4_REVIEW_SAMPLE_INDEX = int(
    os.environ.get("SIMPLE_DOG_VALIDATION_SAMPLE", "0")
) % 5


def _v4_sub_terrains() -> dict:
    return {
        "near_flat": terrain_gen.MeshPlaneTerrainCfg(proportion=0.10),
        "random_rough": terrain_gen.HfRandomUniformTerrainCfg(
            proportion=0.25,
            noise_range=(0.004, 0.030),
            noise_step=0.0025,
            border_width=0.20,
        ),
        "up_slope": terrain_gen.HfPyramidSlopedTerrainCfg(
            proportion=0.10,
            slope_range=(0.04, 0.20),
            platform_width=0.80,
            border_width=0.20,
        ),
        "down_slope": terrain_gen.HfInvertedPyramidSlopedTerrainCfg(
            proportion=0.10,
            slope_range=(0.04, 0.20),
            platform_width=0.80,
            border_width=0.20,
        ),
        "irregular_blocks": terrain_gen.MeshRandomGridTerrainCfg(
            proportion=0.20,
            grid_width=0.14,
            grid_height_range=(0.004, 0.028),
            platform_width=0.80,
        ),
        "up_stairs": terrain_gen.MeshPyramidStairsTerrainCfg(
            proportion=0.125,
            step_height_range=(0.006, 0.022),
            step_width=0.16,
            platform_width=0.80,
            border_width=0.20,
            holes=False,
        ),
        "down_stairs": terrain_gen.MeshInvertedPyramidStairsTerrainCfg(
            proportion=0.125,
            step_height_range=(0.006, 0.022),
            step_width=0.16,
            platform_width=0.80,
            border_width=0.20,
            holes=False,
        ),
    }


V4_TERRAINS = TerrainGeneratorCfg(
    seed=4404,
    curriculum=False,
    size=(4.0, 4.0),
    border_width=2.0,
    num_rows=6,
    num_cols=8,
    horizontal_scale=0.05,
    vertical_scale=0.0025,
    slope_threshold=0.75,
    difficulty_range=(0.65, 1.0),
    use_cache=False,
    sub_terrains=_v4_sub_terrains(),
)


def _v4_review_sub_terrain() -> dict:
    """Build five inspectable V4 surfaces, one for each review instance."""

    terrain_names = (
        "random_rough",
        "up_slope",
        "irregular_blocks",
        "up_stairs",
        "down_stairs",
    )
    source = _v4_sub_terrains()
    terrains = {}
    for terrain_name in terrain_names:
        terrain = copy.deepcopy(source[terrain_name])
        terrain.proportion = 1.0 / len(terrain_names)
        terrains[terrain_name] = terrain
    return terrains


# Headless Isaac Lab video uses ViewerCfg as an absolute world camera.  A
# row of five held-out tiles gives each rollout its own robot instance.  The
# sample index selects both that instance and its close absolute camera.
V4_REVIEW_TERRAIN = TerrainGeneratorCfg(
    seed=4404 + 7919 * V4_REVIEW_SAMPLE_INDEX,
    curriculum=True,
    size=(4.0, 4.0),
    border_width=2.0,
    num_rows=1,
    num_cols=5,
    horizontal_scale=0.05,
    vertical_scale=0.0025,
    slope_threshold=0.75,
    difficulty_range=(
        (0.65, 0.72, 0.80, 0.90, 1.0)[V4_REVIEW_SAMPLE_INDEX],
        (0.65, 0.72, 0.80, 0.90, 1.0)[V4_REVIEW_SAMPLE_INDEX],
    ),
    use_cache=False,
    sub_terrains=_v4_review_sub_terrain(),
)

V4_REVIEW_CAMERA_EYES = (
    (1.25, 0.90, 0.62),
    (1.40, -0.80, 0.58),
    (0.25, 1.20, 0.60),
    (0.25, -1.20, 0.60),
    (1.65, 0.00, 0.55),
)
V4_REVIEW_ENV_ORIGIN_Y = (V4_REVIEW_SAMPLE_INDEX - 2) * 4.0
V4_REVIEW_CAMERA_EYE = (
    V4_REVIEW_CAMERA_EYES[V4_REVIEW_SAMPLE_INDEX][0],
    V4_REVIEW_CAMERA_EYES[V4_REVIEW_SAMPLE_INDEX][1]
    + V4_REVIEW_ENV_ORIGIN_Y,
    V4_REVIEW_CAMERA_EYES[V4_REVIEW_SAMPLE_INDEX][2],
)


V4_EVALUATION_SEGMENTS = (
    ("neutral_uneven", 150, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    ("forward_turn_pitch", 225, 0.18, 0.0, 0.24, 0.0, 0.0, 0.10),
    ("strafe_crouch_turn", 225, 0.0, 0.13, -0.22, -0.030, 0.0, 0.0),
    ("reverse_roll_pitch", 225, -0.15, 0.0, 0.0, 0.0, 0.10, -0.08),
    ("turn_height_roll", 200, 0.0, 0.0, 0.30, 0.015, -0.09, 0.0),
    ("mixed_saturation", 225, 0.24, -0.16, 0.35, -0.035, 0.12, 0.12),
    ("stop", 150, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
)


@configclass
class SimpleDogCurrentBodyV4HardEnvCfg(SimpleDogCurrentV3RoughEnvCfg):
    """First V4 experiment: random start, full-hard terrain and dynamics."""

    policy_family = "current_body_v4"
    observation_history_length = V4_HISTORY_FRAMES
    observation_frame_size = 45
    current_observation_frame_size = V4_FRAME_SIZE
    selected_history_indices = V4_SELECTED_HISTORY_INDICES
    body_command_size = V4_COMMAND_SIZE
    observation_space = len(selected_history_indices) * V4_FRAME_SIZE + body_command_size

    terrain = TerrainImporterCfg(
        prim_path="/World/ground",
        terrain_type="generator",
        terrain_generator=V4_TERRAINS,
        max_init_terrain_level=None,
        collision_group=-1,
        physics_material=sim_utils.RigidBodyMaterialCfg(
            friction_combine_mode="multiply",
            restitution_combine_mode="multiply",
            static_friction=1.0,
            dynamic_friction=0.9,
            restitution=0.0,
        ),
        debug_vis=False,
    )
    terrain_curriculum = False
    suppress_base_contact_termination = True
    pose_goal_training = False
    # Start fully clear of uneven mesh features and let physics settle the
    # robot onto its feet.  This avoids invalid resets with feet embedded in a
    # step or raised block.
    reset_spawn_clearance_m = 0.05
    reset_settle_time_s = 2.0
    reset_hold_randomization_rad = math.radians(15.0)

    command_hold_s = (1.5, 4.0)
    command_smoothing_time_s = 0.35
    posture_hold_s = command_hold_s
    posture_smoothing_time_s = 0.35
    mixed_command_fraction = 0.70
    posture_only_fraction = 0.10
    isolated_motion_fraction = 0.10
    neutral_fraction = 0.10
    command_forward_v4 = (-0.20, 0.24)
    command_lateral_v4 = (-0.16, 0.16)
    command_yaw_v4 = (-0.35, 0.35)
    posture_height_offset = (-0.040, 0.020)
    posture_roll = (-0.14, 0.14)
    posture_pitch = (-0.14, 0.14)
    posture_neutral_fraction = 0.0
    capability_tail_fraction = 0.12
    capability_tail_scale = 1.20

    body_tracking_error_scales = (0.20, 0.15, 0.35, 0.030, 0.12, 0.12)
    body_tracking_kernel_scale = 1.0
    # A command that is ignored must be worse than merely missing positive
    # tracking credit.  At zero progress this term outweighs the maximum
    # +1/s tracking reward; it fades continuously to zero at full progress.
    body_motion_command_threshold = 0.10
    body_motion_shortfall_penalty_scale = -1.25
    domain_randomization_enabled = True
    base_mass_scale = (0.70, 1.35)
    base_mass_delta_kg = (0.0, 0.35)
    link_mass_scale = (0.70, 1.35)
    independent_inertia_scale = (0.70, 1.35)
    actuator_drive_scale = (0.65, 1.35)
    actuator_effort_scale = (0.65, 1.35)
    actuator_velocity_scale = (0.65, 1.35)
    actuator_response_scale = (0.60, 1.40)
    base_com_range = (0.045, 0.045, 0.040)
    robot_static_friction_range = (0.35, 1.40)
    robot_dynamic_friction_range = (0.25, 1.20)
    robot_restitution_range = (0.0, 0.15)
    material_buckets = 64
    current_dropout_probability_max = 0.05
    current_effort_scale_randomization = (0.55, 1.45)
    push_probability = 0.65
    push_interval_s = (1.5, 4.0)
    push_linear_velocity = 0.45
    push_yaw_velocity = 0.40
    reset_small_tilt_deg = 8.0
    reset_large_tilt_deg = 24.0
    reset_large_tilt_fraction = 0.30
    randomize_reset_yaw = True
    observation_noise_enabled = True
    gyro_noise = 0.18
    gravity_noise = 0.05
    joint_position_noise = 0.012
    joint_velocity_noise = 1.20

    timing_interval_ms = (18.0, 39.0)
    timing_reference_ms = 20.0

    current_model_effort_weight = (0.45, 0.75)
    current_model_tracking_weight = (0.10, 0.30)
    current_model_velocity_weight = (0.05, 0.20)
    current_model_memory_weight = (0.05, 0.20)


@configclass
class SimpleDogCurrentBodyV4EvalEnvCfg(SimpleDogCurrentBodyV4HardEnvCfg):
    """One-robot deterministic mixed-command V4 evaluation."""

    scene = copy.deepcopy(SimpleDogCurrentV3RoughEnvCfg().scene)
    evaluation_segments = V4_EVALUATION_SEGMENTS
    episode_length_s = sum(segment[1] for segment in evaluation_segments) / 50.0 + 2.0
    domain_randomization_enabled = False
    push_probability = 0.0
    observation_noise_enabled = False
    current_dropout_probability_max = 0.0
    current_effort_scale_randomization = (1.0, 1.0)
    reset_small_tilt_deg = 0.0
    reset_large_tilt_deg = 0.0
    reset_large_tilt_fraction = 0.0
    randomize_reset_yaw = False


@configclass
class SimpleDogCurrentBodyV4PlayEnvCfg(SimpleDogCurrentBodyV4EvalEnvCfg):
    """Visual review of the exact V4 evaluation task."""

    print_play_metrics = True
    scene = copy.deepcopy(SimpleDogCurrentV3RoughEnvCfg().scene)
    scene.num_envs = 5
    terrain = TerrainImporterCfg(
        prim_path="/World/ground",
        terrain_type="generator",
        terrain_generator=V4_REVIEW_TERRAIN,
        max_init_terrain_level=0,
        collision_group=-1,
        physics_material=sim_utils.RigidBodyMaterialCfg(
            friction_combine_mode="multiply",
            restitution_combine_mode="multiply",
            static_friction=1.0,
            dynamic_friction=0.9,
            restitution=0.0,
        ),
        debug_vis=False,
    )
    viewer: ViewerCfg = ViewerCfg(
        eye=V4_REVIEW_CAMERA_EYE,
        lookat=(0.40, V4_REVIEW_ENV_ORIGIN_Y, 0.14),
        origin_type="world",
    )
