"""Export a deterministic RL-Games V2 actor to a portable NumPy bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import torch


TENSOR_KEYS = {
    "obs_mean": "running_mean_std.running_mean",
    "obs_var": "running_mean_std.running_var",
    "w0": "a2c_network.actor_mlp.0.weight",
    "b0": "a2c_network.actor_mlp.0.bias",
    "w1": "a2c_network.actor_mlp.2.weight",
    "b1": "a2c_network.actor_mlp.2.bias",
    "w2": "a2c_network.actor_mlp.4.weight",
    "b2": "a2c_network.actor_mlp.4.bias",
    "wout": "a2c_network.mu.weight",
    "bout": "a2c_network.mu.bias",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def elu(value: np.ndarray) -> np.ndarray:
    return np.where(value > 0.0, value, np.expm1(value))


def numpy_actor(observation: np.ndarray, arrays: dict[str, np.ndarray]) -> np.ndarray:
    value = (observation - arrays["obs_mean"]) / np.sqrt(arrays["obs_var"] + 1.0e-5)
    value = np.clip(value, -5.0, 5.0)
    for index in range(3):
        value = elu(arrays[f"w{index}"] @ value + arrays[f"b{index}"])
    return np.clip(arrays["wout"] @ value + arrays["bout"], -1.0, 1.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--profile-sha", required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument("--validated-forward-max", type=float, default=0.18)
    parser.add_argument("--validated-yaw-max", type=float, default=0.25)
    args = parser.parse_args()

    if not 0.0 < args.validated_forward_max <= 0.25:
        raise SystemExit("Validated forward limit must be within (0, 0.25] m/s.")
    if not 0.0 < args.validated_yaw_max <= 0.30:
        raise SystemExit("Validated yaw limit must be within (0, 0.30] rad/s.")

    evaluation = json.loads(args.evaluation.read_text(encoding="utf-8"))
    if not evaluation.get("passed"):
        raise SystemExit("Refusing to export a policy without a passing evaluation result.")
    if evaluation.get("stage") != "goal":
        raise SystemExit("Real-robot export requires a passing Goal evaluation.")

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    model = checkpoint["model"]
    arrays = {
        name: model[key].detach().cpu().float().numpy()
        for name, key in TENSOR_KEYS.items()
    }
    expected_shapes = {
        "obs_mean": (180,), "obs_var": (180,),
        "w0": (128, 180), "b0": (128,),
        "w1": (128, 128), "b1": (128,),
        "w2": (128, 128), "b2": (128,),
        "wout": (12, 128), "bout": (12,),
    }
    actual_shapes = {name: value.shape for name, value in arrays.items()}
    if actual_shapes != expected_shapes:
        raise SystemExit(f"Unexpected V2 actor shape: {actual_shapes}")

    generator = np.random.default_rng(42)
    for _ in range(8):
        observation = generator.normal(size=180).astype(np.float32)
        torch_value = torch.from_numpy(observation)
        normalized = torch.clamp(
            (torch_value - model[TENSOR_KEYS["obs_mean"]].float())
            / torch.sqrt(model[TENSOR_KEYS["obs_var"]].float() + 1.0e-5),
            -5.0,
            5.0,
        )
        value = normalized
        for index in range(3):
            value = torch.nn.functional.elu(
                model[TENSOR_KEYS[f"w{index}"]].float() @ value
                + model[TENSOR_KEYS[f"b{index}"]].float()
            )
        expected = torch.clamp(
            model[TENSOR_KEYS["wout"]].float() @ value
            + model[TENSOR_KEYS["bout"]].float(),
            -1.0,
            1.0,
        ).numpy()
        actual = numpy_actor(observation, arrays)
        if not np.allclose(actual, expected, rtol=1.0e-5, atol=1.0e-6):
            raise SystemExit("Portable NumPy actor failed PyTorch parity validation.")

    args.output.mkdir(parents=True, exist_ok=True)
    weights_path = args.output / "policy_weights.npz"
    np.savez_compressed(weights_path, **arrays)
    metadata = {
        "schema_version": 1,
        "profile_id": args.profile_id,
        "profile_sha256": args.profile_sha,
        "checkpoint_sha256": sha256(args.checkpoint),
        "weights_sha256": sha256(weights_path),
        "checkpoint_epoch": int(checkpoint["epoch"]),
        "observation_size": 180,
        "observation_history": 4,
        "observation_frame": [
            "angular_velocity_body_rad_s[3]",
            "projected_gravity_body[3]",
            "body_command_forward_lateral_yaw[3]",
            "policy_joint_position_rad[12]",
            "0.05*policy_joint_velocity_rad_s[12]",
            "previous_applied_action[12]",
        ],
        "action_size": 12,
        "action_clip": [-1.0, 1.0],
        "action_scale_rad": 0.25,
        "control_hz": 50,
        "validated_command_limits": {
            "forward_m_s": [0.0, args.validated_forward_max],
            "lateral_m_s": [0.0, 0.0],
            "yaw_rate_rad_s": [
                -args.validated_yaw_max,
                args.validated_yaw_max,
            ],
        },
        "activation": "elu",
        "normalization_epsilon": 1.0e-5,
        "normalization_clip": [-5.0, 5.0],
        "evaluation": evaluation,
    }
    metadata_path = args.output / "policy_metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"weights": str(weights_path), "metadata": str(metadata_path)}))


if __name__ == "__main__":
    main()
