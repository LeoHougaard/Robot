"""Bounded exact-checkpoint V20 evaluation with immutable source and video evidence.

Run inside the Isaac container after inspecting workloads. This runner owns
only its player process group. It never starts/stops containers or training.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time

from delivery_contract import SEGMENTS
from evaluate_simple_dog_policy import evaluate, read_segments
from snapshot_delivery_run import snapshot


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(checkpoint, profile, fit, suite, seed, source):
    if source.resolve() != Path(__file__).resolve().parent:
        raise ValueError("execute the evaluator from the intended source revision; --source must match its directory")
    for path in (checkpoint, profile, fit):
        if not path.is_file():
            raise ValueError(f"missing input: {path}")
    if "quadruped_current_body_v20_" not in str(checkpoint):
        raise ValueError("delivery evaluation requires an explicitly initialized V20 checkpoint")
    suffix = {"deliveryflat": "Flat-Eval", "delivery": "Eval", "deliverystress": "Stress-Eval"}[suite]
    task = f"Isaac-Locomotion-CurrentBodyV20-{suffix}-Simple-Dog-Direct-v0"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output = checkpoint.parent.parent / "evaluation" / f"{stamp}-{suite}-seed{seed}"
    output.mkdir(parents=True)
    print("Evaluation evidence:", output, flush=True)
    env = os.environ.copy()
    env.update(SIMPLE_DOG_CONTROL_PROFILE=str(profile), SIMPLE_DOG_SIMULATION_FIT=str(fit),
               SIMPLE_DOG_CHECKPOINT=str(checkpoint),
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    old = os.environ.copy()
    try:
        os.environ.update(env)
        snapshot(source, output)
    finally:
        os.environ.clear()
        os.environ.update(old)
    for name in ("evaluate_delivery_policy.py", "evaluate_simple_dog_policy.py", "snapshot_delivery_run.py"):
        shutil.copyfile(source / name, output / "source" / name)
    # A private checkpoint copy gives the stock player a private video folder,
    # preventing another evaluation from overwriting the movie it certifies.
    (output / "nn").mkdir()
    copied = output / "nn" / "evaluated.pth"
    shutil.copyfile(checkpoint, copied)
    shutil.copyfile(profile, output / "control_profile.json")
    shutil.copyfile(fit, output / "simulation_fit.json")
    profile_value = json.loads(profile.read_text())
    source_hashes = {p.relative_to(output / "source").as_posix(): sha(p)
                     for p in (output / "source").rglob("*") if p.is_file()}
    manifest = json.loads((output / "source_manifest.json").read_text())
    provenance = dict(schema_version=1, checkpoint_sha256=sha(copied), original_checkpoint=str(checkpoint),
                      profile_sha256=hashlib.sha256(json.dumps(profile_value, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
                      simulation_fit_sha256=sha(fit), source_files=source_hashes,
                      asset=manifest["inputs"]["asset"], task=task, suite=suite, seed=seed,
                      deterministic=True, control_hz=50,
                      execution_environment=manifest["execution_environment"],
                      command_screen_sha256=sha(source / "delivery_contract.py"),
                      player_sha256=sha("/workspace/isaaclab/scripts/reinforcement_learning/rl_games/play.py"),
                      packages={name: importlib.metadata.version(name) for name in ("torch", "rl-games", "gymnasium")})
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    env.update(SIMPLE_DOG_CONTROL_PROFILE=str(profile),
               SIMPLE_DOG_SIMULATION_FIT=str(fit),
               SIMPLE_DOG_POLICY_FAMILY="current_body_v20", PYTHONPATH=str(output / "source"),
               PYTHONUNBUFFERED="1", SIMPLE_DOG_EVALUATION_EVIDENCE=str(output))
    env.pop("SIMPLE_DOG_CHECKPOINT", None)
    video_steps = sum(segment[1] for segment in SEGMENTS) + 100
    args = ["/workspace/isaaclab/isaaclab.sh", "-p", str(output / "source/play_simple_dog.py"),
            f"--task={task}", f"--checkpoint={copied}", "--num_envs=1", f"--seed={seed}",
            "--deterministic", "--headless", "--video", f"--video_length={video_steps}"]
    (output / "command.json").write_text(json.dumps(args, indent=2) + "\n")
    started = time.monotonic()
    with (output / "console.log").open("w") as log:
        process = subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            code = process.wait(timeout=1200)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    result = evaluate(suite, read_segments(output / "console.log"), require_gait_quality=True)
    if sha(profile) != sha(output / "control_profile.json") or sha(fit) != sha(output / "simulation_fit.json"):
        result["failures"].append("an evaluation input changed during the rollout")
        result["passed"] = False
    asset_inputs = {provenance["asset"]["path"]: provenance["asset"]["sha256"],
                    **provenance["asset"]["source_layers"]}
    if any(not Path(name).is_file() or sha(name) != digest for name, digest in asset_inputs.items()):
        result["failures"].append("an asset or referenced layer changed during the rollout")
        result["passed"] = False
    videos = list((output / "videos/play").glob("*.mp4"))
    if code != 0 or len(videos) != 1 or videos[0].stat().st_size < 1024:
        result["failures"].append(f"incomplete player/video evidence (exit {code}, videos {len(videos)})")
        result["passed"] = False
    result.update(provenance=provenance, player_exit=code, wall_seconds=time.monotonic()-started,
                  console_sha256=sha(output / "console.log"),
                  resolved_config_hashes={name: sha(output / name) for name in ("resolved_env.yaml", "resolved_agent.yaml")
                                          if (output / name).is_file()},
                  videos=[dict(path=str(p.relative_to(output)), sha256=sha(p)) for p in videos],
                  visual_review="pending")
    # Preserve a machine-readable rejection even when the simulator produced
    # NaN/Inf; the original values remain in the hashed console evidence.
    def json_finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        if isinstance(value, dict):
            return {key: json_finite(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [json_finite(item) for item in value]
        return value
    (output / "result.json").write_text(json.dumps(json_finite(result), indent=2, allow_nan=False) + "\n")
    (output / "status").write_text("numeric_pass_visual_pending\n" if result["passed"] else "rejected\n")
    print(json.dumps(dict(passed=result["passed"], failures=result["failures"], evidence=str(output))), flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--fit", type=Path, required=True)
    parser.add_argument("--suite", choices=("deliveryflat", "delivery", "deliverystress"), required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    result = run(args.checkpoint, args.profile, args.fit, args.suite, args.seed, args.source)
    raise SystemExit(0 if result["passed"] else 1)
