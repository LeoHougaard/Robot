"""Expand a validated 38-observation policy with zero-initialized height inputs."""

from __future__ import annotations

import argparse
from pathlib import Path

import torch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--old-observations", type=int, default=38)
    parser.add_argument("--new-observations", type=int, default=73)
    args = parser.parse_args()

    checkpoint = torch.load(args.source, map_location="cpu", weights_only=False)
    model = checkpoint["model"]
    expanded: list[str] = []
    for name, value in tuple(model.items()):
        if not isinstance(value, torch.Tensor):
            continue
        if value.ndim == 2 and value.shape[1] == args.old_observations:
            replacement = value.new_zeros((value.shape[0], args.new_observations))
            replacement[:, : args.old_observations] = value
            model[name] = replacement
            expanded.append(name)
        elif value.ndim == 1 and value.shape[0] == args.old_observations:
            fill = 1.0 if "var" in name.lower() else 0.0
            replacement = value.new_full((args.new_observations,), fill)
            replacement[: args.old_observations] = value
            model[name] = replacement
            expanded.append(name)

    if not any(model[name].ndim == 2 for name in expanded):
        raise RuntimeError("No 38-input network layer was found.")
    optimizer = checkpoint.get("optimizer", {})
    for state in optimizer.get("state", {}).values():
        for name, value in tuple(state.items()):
            if not isinstance(value, torch.Tensor):
                continue
            if value.ndim == 2 and value.shape[1] == args.old_observations:
                replacement = value.new_zeros((value.shape[0], args.new_observations))
                replacement[:, : args.old_observations] = value
                state[name] = replacement
            elif value.ndim == 1 and value.shape[0] == args.old_observations:
                replacement = value.new_zeros((args.new_observations,))
                replacement[: args.old_observations] = value
                state[name] = replacement
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, args.destination)
    print(f"Expanded {len(expanded)} tensors:")
    for name in expanded:
        print(f"  {name}: {tuple(model[name].shape)}")
    print(f"Saved {args.destination}")


if __name__ == "__main__":
    main()
