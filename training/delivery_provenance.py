"""Recheck saved evidence against the exact actor before a delivery export."""
import hashlib
import json
from pathlib import Path

from evaluate_simple_dog_policy import evaluate, read_segments


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def checked_file(directory, relative, digest):
    path = (directory / relative).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file() or sha(path) != digest:
        raise ValueError(f"missing or changed evidence file: {relative}")
    return path


def validate(path, checkpoint_sha, profile_sha, fit_sha, suite):
    result = json.loads(path.read_text())
    provenance = result.get("provenance", {})
    expected = dict(checkpoint_sha256=checkpoint_sha, profile_sha256=profile_sha,
                    simulation_fit_sha256=fit_sha, suite=suite, deterministic=True, control_hz=50)
    if any(provenance.get(key) != value for key, value in expected.items()):
        raise ValueError("evaluation actor, profile, fit or conditions do not match the export")
    if (result.get("stage") != suite or result.get("player_exit") != 0
            or result.get("passed") is not True or result.get("failures")):
        raise ValueError("incomplete or wrong evaluation suite")
    suffix = {"deliveryflat": "Flat-Eval", "delivery": "Eval", "deliverystress": "Stress-Eval"}[suite]
    if provenance.get("task") != f"Isaac-Locomotion-CurrentBodyV20-{suffix}-Simple-Dog-Direct-v0":
        raise ValueError("evaluation task does not implement its declared suite")
    root = path.parent
    console = checked_file(root, "console.log", result["console_sha256"])
    source_files = provenance.get("source_files", {})
    required_source = {"evaluate_simple_dog_policy.py", "delivery_contract.py", "deployable_dynamics.py",
                       "simple_dog_task_current_body_v20/env.py", "simple_dog_task_current_body_v20/env_cfg.py",
                       "fits/servo-response-20260829.json"}
    if not required_source.issubset(source_files):
        raise ValueError("evaluation source was not preserved")
    for name, digest in source_files.items():
        checked_file(root / "source", name, digest)
    for name in required_source:
        if source_files.get(name) != sha(Path(__file__).parent / name):
            raise ValueError("evaluation used a different command screen or acceptance gate")
    fresh = evaluate(suite, read_segments(console), require_gait_quality=True)
    if not fresh["passed"] or fresh["segments"] != result.get("segments"):
        raise ValueError("recorded console does not pass the current fixed gate")
    configs = result.get("resolved_config_hashes", {})
    if set(configs) != {"resolved_env.yaml", "resolved_agent.yaml"}:
        raise ValueError("resolved evaluation configuration is missing")
    for name, digest in configs.items():
        checked_file(root, name, digest)
    videos = result.get("videos", [])
    if len(videos) != 1:
        raise ValueError("evaluation requires its exact rollout video")
    checked_file(root, videos[0]["path"], videos[0]["sha256"])
    review = json.loads((root / "visual_review.json").read_text())
    if (review.get("decision") != "pass" or review.get("checkpoint_sha256") != checkpoint_sha
            or review.get("video_sha256") != videos[0]["sha256"] or not review.get("notes")):
        raise ValueError("the exact actor/video has not passed visual review")
    result["visual_review"] = review
    return result
