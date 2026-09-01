"""CurrentBodyV11 staged terrain/dynamics curriculum; rewards stay unchanged."""

import copy

from isaaclab.utils.configclass import configclass

from simple_dog_task_current_body_v10.simple_dog_current_body_v10_env_cfg import (
    SimpleDogCurrentBodyV10EvalEnvCfg,
    SimpleDogCurrentBodyV10HardEnvCfg,
    SimpleDogCurrentBodyV10PlayEnvCfg,
    SimpleDogCurrentBodyV10PushEvalEnvCfg,
)


_V11_TRAINING_TERRAIN = copy.deepcopy(SimpleDogCurrentBodyV10HardEnvCfg().terrain)
_V11_TRAINING_TERRAIN.max_init_terrain_level = 0


@configclass
class SimpleDogCurrentBodyV11HardEnvCfg(SimpleDogCurrentBodyV10HardEnvCfg):
    """Discover translation low and nominal, then ramp toward full hard."""

    policy_family = "current_body_v11"
    terrain = copy.deepcopy(_V11_TRAINING_TERRAIN)
    difficulty_ramp_floor = 0.01
    difficulty_ramp_full_step = 32 * 200
    difficulty_ramp_terrain_band_rows = 1

    base_mass_scale = (0.90, 1.10)
    base_mass_delta_kg = (0.0, 0.10)
    link_mass_scale = (0.90, 1.10)
    independent_inertia_scale = (0.90, 1.10)
    actuator_drive_scale = (0.85, 1.15)
    actuator_effort_scale = (0.85, 1.15)
    actuator_velocity_scale = (0.85, 1.15)
    actuator_response_scale = (0.85, 1.15)
    base_com_range = (0.020, 0.020, 0.015)
    robot_static_friction_range = (0.70, 1.20)
    robot_dynamic_friction_range = (0.60, 1.00)
    robot_restitution_range = (0.0, 0.05)
    material_buckets = 32
    current_dropout_probability_max = 0.01
    current_effort_scale_randomization = (0.85, 1.15)
    gyro_noise = 0.05
    gravity_noise = 0.015
    joint_position_noise = 0.004
    joint_velocity_noise = 0.35


@configclass
class SimpleDogCurrentBodyV11EvalEnvCfg(SimpleDogCurrentBodyV10EvalEnvCfg):
    """Evaluate V11 on the unchanged deterministic full task."""

    policy_family = "current_body_v11"


@configclass
class SimpleDogCurrentBodyV11PlayEnvCfg(SimpleDogCurrentBodyV10PlayEnvCfg):
    """Five-instance full-rough V11 visual review."""

    policy_family = "current_body_v11"


@configclass
class SimpleDogCurrentBodyV11PushEvalEnvCfg(SimpleDogCurrentBodyV10PushEvalEnvCfg):
    """V11 full-rough recovery evaluation with repeatable physical pushes."""

    policy_family = "current_body_v11"
