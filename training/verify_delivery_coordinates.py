"""Check the CAD actuator graph and bind V22's coordinate contract to its files.

The old serial knee connects femur to tibia. The detailed knee drive connects
hub to servo link; the passive closed loops supply the transmission geometry.
This is a CAD/source audit, not a physical calibration measurement.
"""
import hashlib
import json
from pathlib import Path

from robot_control_profile import canonical_hash


def verify(profile, reference_path, fit_path, expected):
    from pxr import Usd, UsdPhysics

    reference = json.loads(reference_path.read_text())
    asset = Path(profile["robot"]["asset_usd"])
    actual = dict(policy_family="current_body_v22", joint_coordinate_convention="cad_drives_v1",
                  profile_sha256=canonical_hash(profile),
                  reference_sha256=hashlib.sha256(reference_path.read_bytes()).hexdigest(),
                  asset_sha256=hashlib.sha256(asset.read_bytes()).hexdigest(),
                  servo_fit_sha256=hashlib.sha256(fit_path.read_bytes()).hexdigest())
    if actual != expected or reference["joint_coordinate_convention"] != "cad_drives_v1":
        raise ValueError("CAD coordinate contract differs from the actual training files")
    if reference["profile_sha256"] != actual["profile_sha256"]:
        raise ValueError("reference belongs to a different profile")
    stage = Usd.Stage.Open(str(asset), load=Usd.Stage.LoadNone)
    root = stage.GetDefaultPrim().GetPath()
    records = []
    for suffix, corner in (("", "front_right"), ("_LF", "front_left"),
                           ("_RR", "back_right"), ("_LR", "back_left")):
        hub = "_MBK47NygQU_fnvKKJ" + suffix
        expected_pairs = (("hip_abduction", "_MfG1xwFWmgbtmCyEF", hub),
                          ("hip_flexion", "_M5h2pOkgVaejo_GDh" + suffix, hub),
                          ("knee_flexion", hub, "_MTn8HoecFwNHGSxBF" + suffix))
        for semantic, body0, body1 in expected_pairs:
            entry = next(j for j in profile["robot"]["joints"] if j["semantic"] == corner + "_" + semantic)
            prims = [p for p in stage.Traverse() if p.GetName() == entry["name"] and p.IsA(UsdPhysics.RevoluteJoint)]
            if len(prims) != 1:
                raise ValueError("profile does not select one CAD revolute joint")
            prim = prims[0]
            joint = UsdPhysics.Joint(prim)
            if not prim.HasAPI(UsdPhysics.DriveAPI, "angular"):
                raise ValueError("profile selects a passive linkage constraint")
            if joint.GetBody0Rel().GetTargets() != [root.AppendPath("links/" + body0)] or joint.GetBody1Rel().GetTargets() != [root.AppendPath("links/" + body1)]:
                raise ValueError("CAD drive endpoints changed")
            records.append(dict(semantic=entry["semantic"], joint=entry["name"], body0=body0, body1=body1,
                                direction=entry["direction"]))
    return dict(passed=True, contract=actual, driven_joints=records,
                interpretation="Each selected drive is already an actuator angle. No additional hip contribution enters knee targets or feedback.")
