"""First acquisition stage, not a delivery or terrain acceptance test."""
import json
from pathlib import Path

from isaaclab.utils.configclass import configclass
from simple_dog_task_current_body_v20.env_cfg import DeliveryFlatEvalCfg
from delivery_terrain import terrain_for_height

STRIDE_SPEC = json.loads((Path(__file__).resolve().parents[1] /
                         "fits/stride-reference-20260905.json").read_text())


@configclass
class StrideAcquireCfg(DeliveryFlatEvalCfg):
    policy_family = "current_body_v21"
    # Same physical sensor history, plus the controller's sine/cosine clock.
    observation_space = 428
    state_space = 438
    evaluation_segments = ()
    pose_goal_training = False
    print_play_metrics = False
    episode_length_s = 20.
    # Start with the conditions under which the reference was measured.
    # Quantized encoders, causal IMU estimation and measured servos remain.
    stride_command = (.04, 0., 0.)
    terrain_height_fraction = 0.
    terrain = terrain_for_height(terrain_height_fraction)


@configclass
class StrideCommandsCfg(StrideAcquireCfg):
    # First widen only commands. Keep the acquired plane and nominal model.
    stride_command_menu = ((.04, 0., 0.), (-.04, 0., 0.), (0., .02, 0.),
                           (0., -.02, 0.), (0., 0., .1), (0., 0., -.1),
                           (0., 0., 0.), (.04, 0., 0.))
    stride_command_hold_s = (4., 6.)
    stride_initial_forward_s = 4.
    stride_evaluate_commands = False
    # The original 3 cm/s progress threshold excludes the new 2 cm/s lateral
    # command. Use a smaller deadband; credit still caps at requested speed.
    progress_planar_threshold = .005
    progress_yaw_threshold = .01
