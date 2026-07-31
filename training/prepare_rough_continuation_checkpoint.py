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
    return (
        path.suffix == ".pth"
        and relative.parts
        and relative.parts[0]
        in {
            "simple_dog_velocity_direct",
            "simple_dog_rough_velocity_direct",
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--learning-rate", type=float, default=1.0e-4)
    parser.add_argument("--reset-epoch", action="store_true")
    args = parser.parse_args()

    if not allowed_checkpoint(args.source) or not allowed_checkpoint(args.destination):
        raise SystemExit("Source and destination must stay below Simple Dog logs.")
    if not args.source.is_file():
        raise SystemExit(f"Source checkpoint does not exist: {args.source}")
    if args.destination.exists():
        raise SystemExit(f"Refusing to overwrite checkpoint: {args.destination}")
    if not 1.0e-6 <= args.learning_rate <= 1.0e-2:
        raise SystemExit("Learning rate is outside [1e-6, 1e-2].")

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

    args.destination.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, args.destination)
    print(f"source={args.source}")
    print(f"destination={args.destination}")
    print("optimizer_state_entries=0")
    print(f"learning_rate={args.learning_rate}")
    print(f"epoch={checkpoint.get('epoch', 'missing')}")
    print(f"reset_value_stats={','.join(reset_value_stats) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
