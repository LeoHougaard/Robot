"""Print tensor names and shapes from an RL-Games checkpoint."""

from __future__ import annotations

import argparse

import torch


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint")
    args = parser.parse_args()
    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    print(f"top_level={tuple(checkpoint)}")
    for key, value in checkpoint.items():
        if key == "model":
            continue
        shape = tuple(value.shape) if hasattr(value, "shape") else ""
        detail = repr(value) if isinstance(value, (int, float, str, bool)) else shape
        print(f"top {key}: {type(value).__name__} {detail}")
    for key, value in checkpoint.get("model", {}).items():
        shape = tuple(value.shape) if hasattr(value, "shape") else ""
        print(f"model {key}: {shape}")


if __name__ == "__main__":
    main()
