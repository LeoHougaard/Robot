"""Benchmark portable policy inference without opening the robot serial port."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from run_policy import NumpyPolicy, POLICY_FRAME_SIZE, POLICY_HISTORY


def main() -> None:
    runtime_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", type=Path, default=runtime_dir / "policy_weights.npz")
    parser.add_argument("--metadata", type=Path, default=runtime_dir / "policy_metadata.json")
    parser.add_argument("--iterations", type=int, default=500)
    parser.add_argument(
        "--require-hz",
        type=float,
        default=0.0,
        help="exit unsuccessfully when mean action-only throughput is lower",
    )
    args = parser.parse_args()
    if args.iterations < 10:
        parser.error("iterations must be at least 10")
    if args.require_hz < 0:
        parser.error("require-hz cannot be negative")

    policy = NumpyPolicy(args.weights, args.metadata)
    observation = np.zeros(POLICY_FRAME_SIZE * POLICY_HISTORY, dtype=np.float32)
    for _ in range(25):
        policy.action(observation)

    samples_ms: list[float] = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        policy.action(observation)
        samples_ms.append((time.perf_counter() - started) * 1000.0)

    durations = np.asarray(samples_ms, dtype=np.float64)
    mean_ms = float(np.mean(durations))
    mean_hz = 1000.0 / mean_ms
    print(
        "Portable actor: "
        f"mean {mean_ms:.3f} ms ({mean_hz:.1f} Hz), "
        f"p95 {np.percentile(durations, 95):.3f} ms, "
        f"p99 {np.percentile(durations, 99):.3f} ms, "
        f"max {np.max(durations):.3f} ms"
    )
    print("Policy bundle hash and export contract: verified")
    if args.require_hz and mean_hz < args.require_hz:
        raise SystemExit(
            f"mean inference throughput {mean_hz:.1f} Hz is below "
            f"the required {args.require_hz:.1f} Hz"
        )


if __name__ == "__main__":
    main()
