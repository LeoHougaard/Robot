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
