"""Load the exact control-center profile selected for an Isaac Lab run."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


PROFILE_ENV = "SIMPLE_DOG_CONTROL_PROFILE"
ALLOWED_ROOT = Path("/workspace/projects/training/control_profiles")
ALLOWED_ASSET_ROOT = Path("/workspace/projects/assets/onshape")


def load_control_profile() -> dict[str, Any] | None:
    path_text = os.environ.get(PROFILE_ENV, "")
    if not path_text:
        return None
    path = Path(path_text)
    try:
        path.resolve().relative_to(ALLOWED_ROOT)
    except ValueError as exc:
        raise ValueError(f"{PROFILE_ENV} must be below {ALLOWED_ROOT}.") from exc
    with path.open("r", encoding="utf-8") as handle:
        profile = json.load(handle)
    if profile.get("schema_version") != 1:
        raise ValueError("Unsupported robot control profile schema.")
    robot = profile.get("robot", {})
    joints = robot.get("joints", [])
    if not robot.get("ready_for_training"):
        raise ValueError("The selected robot profile is not ready for training.")
    if len(joints) != robot.get("expected_joint_count"):
        raise ValueError("The profile joint map does not match expected_joint_count.")
    asset_source = robot.get("asset_source")
    asset_value = str(robot.get("asset_usd", ""))
    if asset_source == "Isaac Lab built-in":
        if asset_value != "isaaclab://Robots/Unitree/Go2/go2.usd":
            raise ValueError("The built-in robot must use the installed Unitree Go2 asset.")
    elif asset_source == "Workspace USD":
        asset_path = Path(asset_value)
        try:
            asset_path.resolve().relative_to(ALLOWED_ASSET_ROOT)
        except ValueError as exc:
            raise ValueError(
                "The profile robot asset is outside the dedicated Onshape asset directory."
            ) from exc
        if not asset_path.is_file():
            raise ValueError(f"The selected robot USD does not exist: {asset_path}")
    else:
        raise ValueError("Unsupported robot asset source.")
    serialized = json.dumps(robot, ensure_ascii=False)
    if "REPLACE_" in serialized.upper() or "replace-me" in serialized.lower():
        raise ValueError("The selected robot profile still contains template placeholders.")
    return profile


def value(profile: dict[str, Any] | None, path: str, default: Any) -> Any:
    if profile is None:
        return default
    current: Any = profile
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def canonical_hash(profile: dict[str, Any]) -> str:
    encoded = json.dumps(
        profile, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def apply_agent_profile(
    agent_cfg: dict[str, Any], profile: dict[str, Any] | None
) -> dict[str, Any]:
    if profile is None:
        return agent_cfg
    config = agent_cfg["params"]["config"]
    seed_override = os.environ.get("SIMPLE_DOG_SEED_OVERRIDE", "")
    if seed_override:
        if not seed_override.isdecimal():
            raise ValueError("SIMPLE_DOG_SEED_OVERRIDE must be a non-negative integer.")
        agent_cfg["params"]["seed"] = int(seed_override)
    else:
        agent_cfg["params"]["seed"] = profile["training"]["seed"]
    policy_family = os.environ.get("SIMPLE_DOG_POLICY_FAMILY")
    if policy_family in (
        "current_v3", "current_body_v4", "current_body_v5",
        "current_body_v6", "current_body_v7", "current_body_v8", "current_body_v9",
        "current_body_v10",
        "current_body_v11",
        "current_body_v12",
        "current_body_v13",
        "current_body_v14",
        "current_body_v15",
        "current_body_v16",
        "current_body_v17",
        "current_body_v18",
        "current_body_v19",
    ):
        # The selected profile owns robot geometry and hardware limits, but its
        # PPO block was tuned for the 180-input V2 family. Keep CurrentV3's
        # separately reviewed optimizer/network contract and namespace.
        prefix = {
            "current_v3": "quadruped_current_v3_",
            "current_body_v4": "quadruped_current_body_v4_",
            "current_body_v5": "quadruped_current_body_v5_",
            "current_body_v6": "quadruped_current_body_v6_",
            "current_body_v7": "quadruped_current_body_v7_",
            "current_body_v8": "quadruped_current_body_v8_",
            "current_body_v9": "quadruped_current_body_v9_",
            "current_body_v10": "quadruped_current_body_v10_",
            "current_body_v11": "quadruped_current_body_v11_",
            "current_body_v12": "quadruped_current_body_v12_",
            "current_body_v13": "quadruped_current_body_v13_",
            "current_body_v14": "quadruped_current_body_v14_",
            "current_body_v15": "quadruped_current_body_v15_",
            "current_body_v16": "quadruped_current_body_v16_",
            "current_body_v17": "quadruped_current_body_v17_",
            "current_body_v18": "quadruped_current_body_v18_",
            "current_body_v19": "quadruped_current_body_v19_",
        }[policy_family]
        config["name"] = prefix + profile["profile_id"].replace("-", "_")
        return agent_cfg
    ppo = profile["ppo"]
    config["name"] = "quadruped_v2_" + profile["profile_id"].replace("-", "_")
    config.update(
        learning_rate=ppo["learning_rate"],
        lr_schedule=(
            None
            if ppo["learning_rate_schedule"] == "fixed"
            else ppo["learning_rate_schedule"]
        ),
        horizon_length=ppo["horizon_length"],
        minibatch_size=ppo["minibatch_size"],
        mini_epochs=ppo["mini_epochs"],
        gamma=ppo["gamma"],
        tau=ppo["gae_lambda"],
        entropy_coef=ppo["entropy_coefficient"],
        e_clip=ppo["clip_range"],
        kl_threshold=ppo["kl_threshold"],
        grad_norm=ppo["grad_norm"],
        critic_coef=ppo["critic_coefficient"],
    )
    network = agent_cfg["params"]["network"]
    network["mlp"]["units"] = list(ppo["hidden_units"])
    network["mlp"]["activation"] = ppo["activation"]
    return agent_cfg
