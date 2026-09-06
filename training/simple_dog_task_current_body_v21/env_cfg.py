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
