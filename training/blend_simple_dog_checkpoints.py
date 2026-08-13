"""Blend compatible RL-Games checkpoints for deterministic policy selection."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

import torch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_a", type=Path)
    parser.add_argument("source_b", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--b-weight", type=float, required=True)
    args = parser.parse_args()
    if not 0.0 <= args.b_weight <= 1.0:
        raise ValueError("--b-weight must be between zero and one")

    checkpoint_a = torch.load(args.source_a, map_location="cpu", weights_only=False)
    checkpoint_b = torch.load(args.source_b, map_location="cpu", weights_only=False)
    model_a = checkpoint_a.get("model")
    model_b = checkpoint_b.get("model")
    if not isinstance(model_a, dict) or not isinstance(model_b, dict):
        raise ValueError("both checkpoints must contain RL-Games model state")
    if model_a.keys() != model_b.keys():
        raise ValueError("checkpoint model keys differ")

    blended = deepcopy(checkpoint_a)
    output_model = blended["model"]
    for key in model_a:
        value_a = model_a[key]
        value_b = model_b[key]
        if not isinstance(value_a, torch.Tensor) or not isinstance(value_b, torch.Tensor):
            if value_a != value_b:
                raise ValueError(f"non-tensor model value differs: {key}")
            continue
        if value_a.shape != value_b.shape or value_a.dtype != value_b.dtype:
            raise ValueError(f"incompatible model tensor: {key}")
        if value_a.is_floating_point():
            output_model[key] = torch.lerp(value_a, value_b, args.b_weight)
        elif not torch.equal(value_a, value_b):
            raise ValueError(f"non-floating model tensor differs: {key}")

    blended["epoch"] = max(
        int(checkpoint_a.get("epoch", 0)), int(checkpoint_b.get("epoch", 0))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(blended, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
