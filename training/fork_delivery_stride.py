"""Make an explicit high-lift checkpoint fork without changing learned tensors."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

import torch
import yaml


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _same(a, b):
    if torch.is_tensor(a) and torch.is_tensor(b):
        return a.shape == b.shape and a.dtype == b.dtype and torch.equal(a, b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return type(a) is type(b) and len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    return a == b


def fork(source_checkpoint, config, old_reference, reference, output_checkpoint, manifest):
    if output_checkpoint.exists() or manifest.exists():
        raise ValueError("refusing to overwrite fork output")
    params = yaml.safe_load(config.read_text())
    new_contract = params["params"]["config"]["delivery_policy_contract"]
    ref = json.loads(reference.read_text())
    old_ref = json.loads(old_reference.read_text())
    actual_ref_sha = sha256(reference)
    if ref.get("kind") != "stride_reference_cad_v2":
        raise ValueError("fork reference must be stride_reference_cad_v2")
    for key in ("lift_full_planar_speed_m_s", "lift_full_yaw_rate_rad_s", "lift_m"):
        if key not in ref:
            raise ValueError(f"fork reference missing {key}")
    if new_contract["reference_sha256"] != actual_ref_sha:
        raise ValueError("config reference_sha256 does not match reference file")
    source = torch.load(source_checkpoint, map_location="cpu", weights_only=False)
    old_contract = source.get("delivery_policy_contract")
    if not isinstance(old_contract, dict):
        raise ValueError("source checkpoint has no delivery_policy_contract")
    if set(old_contract) != set(new_contract):
        raise ValueError("contract key set changed outside reference_sha256")
    for key in ("policy_family", "joint_coordinate_convention", "profile_sha256", "asset_sha256", "servo_fit_sha256"):
        if old_contract.get(key) != new_contract.get(key):
            raise ValueError(f"contract changed outside reference_sha256: {key}")
    if any(old_contract[k] != new_contract[k] for k in old_contract if k != "reference_sha256"):
        raise ValueError("contract changed outside reference_sha256")
    if old_contract.get("reference_sha256") == actual_ref_sha:
        raise ValueError("fork reference hash did not change")
    if old_contract.get("reference_sha256") != sha256(old_reference):
        raise ValueError("source contract does not match --old-reference")
    def without_changes(spec):
        return {k: v for k, v in spec.items() if k not in {"kind", "lift_m",
            "lift_full_planar_speed_m_s", "lift_full_yaw_rate_rad_s"}}
    if without_changes(old_ref) != without_changes(ref):
        raise ValueError("reference changed geometry or gait calibration outside the high-lift fields")
    if old_ref.get("kind") != "stride_reference_cad_v1":
        raise ValueError("--old-reference must be stride_reference_cad_v1")
    child = copy.deepcopy(source)
    child["delivery_policy_contract"] = copy.deepcopy(new_contract)
    child["last_mean_rewards"] = -1e9
    for key in source:
        if key in ("delivery_policy_contract", "last_mean_rewards"):
            continue
        if not _same(child[key], source[key]):
            raise AssertionError(f"preserved field changed: {key}")
    for key in ("model", "optimizer", "epoch", "frame"):
        if key not in source:
            raise ValueError(f"source checkpoint missing preserved field {key}")
    output_checkpoint.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    torch.save(child, output_checkpoint)
    roundtrip = torch.load(output_checkpoint, map_location="cpu", weights_only=False)
    for key in source:
        if key in ("delivery_policy_contract", "last_mean_rewards"):
            continue
        if not _same(roundtrip[key], source[key]):
            raise AssertionError(f"round-trip changed preserved field: {key}")
    if roundtrip.get("delivery_policy_contract") != new_contract or roundtrip.get("last_mean_rewards") != -1e9:
        raise AssertionError("round-trip fork contract/reward reset mismatch")
    record = {
        "kind": "delivery_stride_controller_fork",
        "parent_checkpoint_sha256": sha256(source_checkpoint),
        "checkpoint_sha256": sha256(output_checkpoint),
        "config_sha256": sha256(config),
        "source_checkpoint_path": str(source_checkpoint),
        "old_reference_path": str(old_reference),
        "reference_path": str(reference),
        "old_reference_sha256": sha256(old_reference),
        "reference_sha256": actual_ref_sha,
        "old_contract": old_contract,
        "new_contract": new_contract,
        "reference_kind": ref["kind"],
        "preserved_fields": ["model", "optimizer", "normalization", "epoch", "frame"],
        "reset_fields": {"last_mean_rewards": -1e9},
        "declared_experiment": "changed_controller_highlift; not a same-policy resume",
    }
    manifest.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--old-reference", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output-checkpoint", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    fork(args.source_checkpoint, args.config, args.old_reference, args.reference,
         args.output_checkpoint, args.manifest)
