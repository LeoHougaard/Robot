#!/usr/bin/env python3
"""Keep learned policy weights while resetting stale PPO continuation state."""

from __future__ import annotations

import argparse
from pathlib import Path

import torch


ALLOWED_ROOT = Path("/workspace/projects/training/logs/rl_games")


def allowed_checkpoint(path: Path) -> bool:
    try:
        relative = path.resolve().relative_to(ALLOWED_ROOT)
    except ValueError:
        return False
    if path.suffix != ".pth" or not relative.parts:
        return False
    experiment = relative.parts[0]
    return experiment in {
        "simple_dog_velocity_direct",
        "simple_dog_rough_velocity_direct",
    } or experiment.startswith("quadruped_v2_")


def experiment_namespace(path: Path) -> str:
    return path.resolve().relative_to(ALLOWED_ROOT).parts[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--learning-rate", type=float, default=1.0e-4)
    parser.add_argument("--reset-epoch", action="store_true")
    parser.add_argument(
        "--reset-observation-indices",
        default="",
        help="Comma-separated actor observation indices whose mean/variance should be reset.",
    )
    parser.add_argument(
        "--observation-variance", type=float, default=1.0,
        help="Variance assigned to reset actor observation indices.",
    )
    args = parser.parse_args()

    if not allowed_checkpoint(args.source) or not allowed_checkpoint(args.destination):
        raise SystemExit("Source and destination must stay below Simple Dog logs.")
    if experiment_namespace(args.source) != experiment_namespace(args.destination):
        raise SystemExit("Source and destination must use the same robot experiment namespace.")
    if not args.source.is_file():
        raise SystemExit(f"Source checkpoint does not exist: {args.source}")
    if args.destination.exists():
        raise SystemExit(f"Refusing to overwrite checkpoint: {args.destination}")
    if not 1.0e-6 <= args.learning_rate <= 1.0e-2:
        raise SystemExit("Learning rate is outside [1e-6, 1e-2].")
    if args.observation_variance <= 0.0:
        raise SystemExit("Observation variance must be positive.")
    try:
        observation_indices = [
            int(value) for value in args.reset_observation_indices.split(",")
            if value.strip()
        ]
    except ValueError as exc:
        raise SystemExit("Observation indices must be comma-separated integers.") from exc

    checkpoint = torch.load(args.source, map_location="cpu", weights_only=False)
    optimizer = checkpoint.get("optimizer")
    if not isinstance(optimizer, dict) or not isinstance(
        optimizer.get("param_groups"), list
    ):
        raise SystemExit("Checkpoint has no compatible optimizer state.")
    optimizer["state"] = {}
    for group in optimizer["param_groups"]:
        group["lr"] = args.learning_rate
        group["initial_lr"] = args.learning_rate

    checkpoint.pop("scaler", None)
    checkpoint["last_mean_rewards"] = -1_000_000_000.0
    if args.reset_epoch:
        checkpoint["epoch"] = 0

    reset_value_stats: list[str] = []
    reset_observation_stats: list[str] = []
    model = checkpoint.get("model", {})
    for key, value in model.items():
        if not isinstance(value, torch.Tensor) or "value_mean_std" not in key:
            continue
        if key.endswith("running_mean"):
            value.zero_()
            reset_value_stats.append(key)
        elif key.endswith("running_var"):
            value.fill_(1.0)
            reset_value_stats.append(key)
        elif key.endswith("count"):
            value.fill_(1.0)
            reset_value_stats.append(key)

    for key, value in model.items():
        if not isinstance(value, torch.Tensor) or "running_mean_std" not in key:
            continue
        if "value_mean_std" in key or not observation_indices:
            continue
        if value.ndim != 1:
            continue
        if any(index < 0 or index >= value.shape[0] for index in observation_indices):
            raise SystemExit(
                f"Observation index is outside {key} with length {value.shape[0]}."
            )
        if key.endswith("running_mean"):
            value[observation_indices] = 0.0
            reset_observation_stats.append(key)
        elif key.endswith("running_var"):
            value[observation_indices] = args.observation_variance
            reset_observation_stats.append(key)

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, args.destination)
    print(f"source={args.source}")
    print(f"destination={args.destination}")
    print("optimizer_state_entries=0")
    print(f"learning_rate={args.learning_rate}")
    print(f"epoch={checkpoint.get('epoch', 'missing')}")
    print(f"reset_value_stats={','.join(reset_value_stats) or 'none'}")
    print(
        "reset_observation_stats="
        f"{','.join(reset_observation_stats) or 'none'}"
    )
    print(
        "reset_observation_indices="
        f"{','.join(str(index) for index in observation_indices) or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
