"""CAD-driven linkage coordinates, isolated from the V21 mapping mismatch."""
from isaaclab.utils.configclass import configclass
from simple_dog_task_current_body_v21.env_cfg import StrideAcquireCfg, StrideCommandsCfg


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
