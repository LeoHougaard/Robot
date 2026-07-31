#!/usr/bin/env python3
"""Validate that an Isaac Lab rollout contains usable visual evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def inspect_video(path: Path) -> dict[str, float | int | bool | str]:
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        return {"valid": False, "reason": "video could not be opened", "frames": 0}

    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    stride = max(1, frame_count // 40)
    means: list[float] = []
    contrasts: list[float] = []
    motions: list[float] = []
    previous: np.ndarray | None = None
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        if index % stride == 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            means.append(float(gray.mean()))
            contrasts.append(float(gray.std()))
            if previous is not None:
                motions.append(float(cv2.absdiff(gray, previous).mean()))
            previous = gray
        index += 1
    capture.release()

    sampled = len(means)
    mean_luma = float(np.mean(means)) if means else 0.0
    mean_contrast = float(np.mean(contrasts)) if contrasts else 0.0
    mean_motion = float(np.mean(motions)) if motions else 0.0
    valid = (
        sampled >= 2
        and mean_luma >= 3.0
        and mean_contrast >= 1.0
        and mean_motion >= 0.02
    )
    reasons: list[str] = []
    if sampled < 2:
        reasons.append("fewer than two decodable frames")
    if mean_luma < 3.0:
        reasons.append("frames are black")
    if mean_contrast < 1.0:
        reasons.append("frames have no visible contrast")
    if mean_motion < 0.02:
        reasons.append("frames are frozen")
    return {
        "valid": valid,
        "reason": "usable visual evidence" if valid else "; ".join(reasons),
        "frames": index,
        "sampled_frames": sampled,
        "mean_luma": mean_luma,
        "mean_contrast": mean_contrast,
        "mean_motion": mean_motion,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    args = parser.parse_args()
    result = inspect_video(args.video)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["valid"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
