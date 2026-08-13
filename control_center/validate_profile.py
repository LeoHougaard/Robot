"""Validate a control profile for PowerShell and CI callers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .model import load_profile, profile_hash, validate_profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    parser.add_argument("--launch", action="store_true")
    args = parser.parse_args()
    try:
        profile = load_profile(args.profile.resolve())
        result = validate_profile(profile, for_launch=args.launch)
        payload = {
            "ok": not result["errors"],
            "hash": profile_hash(profile),
            "profile_id": profile["profile_id"],
            "stage": profile["training"]["stage"],
            "num_envs": profile["training"]["num_envs"],
            "max_iterations": profile["training"]["max_iterations"],
            "checkpoint": profile["training"]["checkpoint"],
            "record_video": profile["training"]["record_video"],
            "video_interval": profile["training"]["video_interval"],
            "video_length": profile["training"]["video_length"],
            **result,
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        payload = {"ok": False, "errors": [str(exc)], "warnings": []}
    print(json.dumps(payload, separators=(",", ":")))
    if not payload["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
