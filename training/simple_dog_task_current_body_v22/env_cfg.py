"""CAD-driven linkage coordinates, isolated from the V21 mapping mismatch."""
from isaaclab.utils.configclass import configclass
from simple_dog_task_current_body_v21.env_cfg import StrideAcquireCfg, StrideCommandsCfg
from delivery_terrain import terrain_for_height


@configclass
class CadStrideAcquireCfg(StrideAcquireCfg):
    policy_family = "current_body_v22"
    joint_coordinate_convention = "cad_drives_v1"
    stride_reference_filename = "stride-reference-cad-20260906.json"
    # Preserve the reference's straight heading from the first PPO update.
    # V21's weaker yaw cost admitted a learned curve despite good speed.
    yaw_tracking_variance = .01
    stationary_contact_penalty_scale = .25


@configclass
class CadStrideCommandsCfg(StrideCommandsCfg):
    policy_family = "current_body_v22"
    joint_coordinate_convention = "cad_drives_v1"
    stride_reference_filename = "stride-reference-cad-20260906.json"
    moving_foot_duration_penalty_scale = .4


@configclass
class CadStrideSpeedCfg(CadStrideCommandsCfg):
    """Bounded flat speed continuation from the accepted V22 epoch 2750 line.

    The slow rows remain in the menu to train retention of the known walking
    behavior; matched evaluations check for regressions. This nominal
    speed stage keeps the 20 ms simulation timing; sensor-age/action-delay
    stress remains a separately labeled evaluation condition.
    """
    stride_command_menu = (
        (.04, 0., 0.), (-.04, 0., 0.),
        (0., .02, 0.), (0., -.02, 0.),
        (0., 0., .10), (0., 0., -.10),
        (0., 0., 0.), (.04, 0., 0.),
        (.06, 0., 0.), (-.06, 0., 0.),
        (0., .03, 0.), (0., -.03, 0.),
        (0., 0., .15), (0., 0., -.15),
    )


@configclass
class CadStrideVariationCfg(CadStrideCommandsCfg):
    """Flat V22 command screen with the documented V20 physical envelope.

    This is an evaluation-only condition.  The nominal Commands config above
    remains unchanged so its checkpoint comparison is byte-for-byte compatible.
    V20's documented ranges are activated through the existing environment
    randomizers; pushes and terrain changes remain disabled for attribution.
    """
    domain_randomization_enabled = True
    observation_noise_enabled = True
    current_dropout_probability_max = .02
    current_effort_scale_randomization = (.75, 1.25)
    push_probability = 0.


@configclass
class CadStrideRobustCfg(CadStrideVariationCfg):
    """Bounded robust continuation from the accepted V22 Commands policy."""
    # Aug-29 feedback timing: median 23 ms, p95 31 ms, max 39 ms. The
    # timing feature is sensor age; the action delay is a separate causal
    # delivery buffer and the servo trajectory adds its measured delay.
    timing_interval_ms = (18., 39.)
    action_delay_steps = (0, 1)
    # Activate the full documented envelope from the first robust stage step.
    difficulty_ramp_floor = 1.
    difficulty_ramp_full_step = 1


@configclass
class CadStrideSustainedCfg(CadStrideRobustCfg):
    """Robust continuation with long command holds for drift exposure."""
    episode_length_s = 70.
    stride_command_hold_s = (4., 60.)


@configclass
class CadStrideRough125Cfg(CadStrideSustainedCfg):
    """Fixed exploratory rough mixture at one-eighth source height.

    This stage keeps the 428-observation actor contract and Sustained
    randomization/holds. Terrain difficulty is fixed; advancement requires
    separate held-out per-kind gates and does not imply promotion.
    """
    stride_command_menu = CadStrideSpeedCfg().stride_command_menu
    terrain_height_fraction = .125
    terrain = terrain_for_height(terrain_height_fraction, tile_size=8.0)
    terrain_curriculum = False
