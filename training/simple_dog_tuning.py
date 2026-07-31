"""Validated runtime tuning for autonomous Simple Dog experiments.

Only parameters listed here may be changed by the autoresearch supervisor.
The immutable robot asset, termination logic, contact topology, and executable
code remain outside the model-controlled surface.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULTS: dict[str, float] = {
    "command_forward_min": 0.15,
    "command_forward_max": 0.30,
    "body_vel_reward_scale": 5.0,
    "velocity_tracking_std": 0.20,
    "yaw_rate_reward_scale": 2.0,
    "gait_reward_scale": 5.0,
    "feet_air_time_reward_scale": 10.0,
    "air_time_variance_penalty_scale": -1.0,
    "base_motion_penalty_scale": -2.0,
    "base_orientation_penalty_scale": -3.0,
    "action_smoothness_penalty_scale": -1.0,
    "foot_slip_penalty_scale": -2.0,
    "undesired_contact_penalty_scale": -1.0,
}

RANGES: dict[str, tuple[float, float]] = {
    "command_forward_min": (0.05, 0.40),
    "command_forward_max": (0.10, 0.50),
    "body_vel_reward_scale": (1.0, 10.0),
    "velocity_tracking_std": (0.05, 0.50),
    "yaw_rate_reward_scale": (0.25, 5.0),
    "gait_reward_scale": (0.0, 10.0),
    "feet_air_time_reward_scale": (0.0, 15.0),
    "air_time_variance_penalty_scale": (-4.0, 0.0),
    "base_motion_penalty_scale": (-6.0, 0.0),
    "base_orientation_penalty_scale": (-8.0, 0.0),
    "action_smoothness_penalty_scale": (-4.0, 0.0),
    "foot_slip_penalty_scale": (-6.0, 0.0),
    "undesired_contact_penalty_scale": (-5.0, 0.0),
}


def validate_tuning(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise ValueError("Tuning configuration must be a JSON object.")
    unknown = sorted(set(raw) - set(RANGES))
    if unknown:
        raise ValueError(f"Unknown tuning keys: {', '.join(unknown)}")

    result: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric.")
        numeric = float(value)
        low, high = RANGES[key]
        if not low <= numeric <= high:
            raise ValueError(f"{key}={numeric} is outside [{low}, {high}].")
        result[key] = numeric

    merged = DEFAULTS | result
    if merged["command_forward_min"] > merged["command_forward_max"]:
        raise ValueError("command_forward_min cannot exceed command_forward_max.")
    return result


def load_tuning() -> dict[str, float]:
    path_text = os.environ.get("SIMPLE_DOG_TUNING_CONFIG", "")
    if not path_text:
        return DEFAULTS.copy()
    path = Path(path_text)
    allowed_root = Path("/workspace/projects/autoresearch")
    try:
        path.resolve().relative_to(allowed_root)
    except ValueError as exc:
        raise ValueError(
            "SIMPLE_DOG_TUNING_CONFIG must be below "
            "/workspace/projects/autoresearch."
        ) from exc
    with path.open("r", encoding="utf-8") as handle:
        overrides = validate_tuning(json.load(handle))
    return DEFAULTS | overrides


if __name__ == "__main__":
    active = load_tuning()
    print(json.dumps(active, indent=2, sort_keys=True))
