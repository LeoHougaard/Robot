"""Configuration for the deliberately small Simple Dog V2 locomotion task."""

from isaaclab.scene import InteractiveSceneCfg
from isaaclab.utils.configclass import configclass

from simple_dog_task.simple_dog_env_cfg import SimpleDogFlatEnvCfg


@configclass
class SimpleDogV2CoreEnvCfg(SimpleDogFlatEnvCfg):
    """Learn sustained, steerable flat locomotion with mild recovery demands."""

    # Four frames of deployable proprioception:
    # gyro(3), projected gravity(3), command(3), joint pos/vel(16), action(8).
    observation_history_length = 4
    observation_frame_size = 33
    observation_space = observation_history_length * observation_frame_size
    state_space = 0

    scene: InteractiveSceneCfg = InteractiveSceneCfg(
        num_envs=512,
        env_spacing=1.5,
        replicate_physics=True,
    )

    # Smoothly changing forward/yaw commands describe locally curved paths.
    command_forward = (0.15, 0.30)
    command_lateral = (0.0, 0.0)
    command_yaw = (-0.40, 0.40)
    standing_command_fraction = 0.0
    turn_command_fraction = 0.0
    turn_yaw_rate = (0.30, 0.60)
    command_hold_s = (2.0, 4.0)
    command_smoothing_time_s = 0.40

    # Most starts are nearly upright. Recovery from genuinely fallen poses is
    # intentionally a separate future task.
    reset_small_tilt_deg = 5.0
    reset_large_tilt_deg = 10.0
    reset_large_tilt_fraction = 0.10
    randomize_reset_yaw = True

    # Mild stumble recovery without turning locomotion into a get-up task.
    push_interval_s = (6.0, 10.0)
    push_probability = 0.20
    push_linear_velocity = 0.10
    push_yaw_velocity = 0.10

    # Plausible sensor corruption for the deployable actor.
    observation_noise_enabled = True
    gyro_noise = 0.10
    gravity_noise = 0.02
    joint_position_noise = 0.01
    joint_velocity_noise = 0.50

    # One main locomotion objective and a small number of regularizers.
    locomotion_reward_scale = 4.0
    velocity_tracking_std = 0.20
    yaw_reward_scale = 0.50
    yaw_tracking_std = 0.50
    diagonal_gait_reward_scale = 0.40
    diagonal_gait_std = 0.10
    stability_penalty_scale = -0.50
    action_rate_penalty_scale = -0.02
    foot_slip_penalty_scale_v2 = -0.25
    undesired_contact_penalty_scale_v2 = -1.0
    fall_penalty_scale_v2 = -8.0


@configclass
class SimpleDogV2RobustEnvCfg(SimpleDogV2CoreEnvCfg):
    """Continue a proven V2 core policy with stronger tilt and pushes."""

    reset_small_tilt_deg = 10.0
    reset_large_tilt_deg = 20.0
    reset_large_tilt_fraction = 0.20
    push_probability = 0.35
    push_linear_velocity = 0.30
    push_yaw_velocity = 0.25
    gyro_noise = 0.15
    gravity_noise = 0.04
    joint_velocity_noise = 1.00


@configclass
class SimpleDogV2GoalEnvCfg(SimpleDogV2RobustEnvCfg):
    """Add the stop and turn-in-place commands needed by a pose controller."""

    command_forward = (0.05, 0.30)
    standing_command_fraction = 0.15
    turn_command_fraction = 0.15


@configclass
class SimpleDogV2PlayEnvCfg(SimpleDogV2CoreEnvCfg):
    """Deterministic straight-line acceptance task for a V2 checkpoint."""

    episode_length_s = 60.0
    scene: InteractiveSceneCfg = InteractiveSceneCfg(
        num_envs=1,
        env_spacing=1.5,
        replicate_physics=True,
    )
    command_forward = (0.25, 0.25)
    command_yaw = (0.0, 0.0)
    command_hold_s = (60.0, 60.0)
    command_smoothing_time_s = 0.01
    reset_small_tilt_deg = 0.0
    reset_large_tilt_deg = 0.0
    reset_large_tilt_fraction = 0.0
    randomize_reset_yaw = False
    push_probability = 0.0
    observation_noise_enabled = False
    print_play_metrics = True
