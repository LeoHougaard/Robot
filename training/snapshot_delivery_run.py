"""Freeze the exact delivery source and input hashes before a training run."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys


def snapshot(root, run):
    destination = run / "source"
    destination.mkdir()
    files = ["train_simple_dog.py", "play_simple_dog.py", "robot_control_profile.py",
             "simple_dog_tuning.py", "pose_goal_controller.py", "terrain_curriculum.py",
             "video_camera.py", "v4_difficulty_ramp.py", "current_policy_fit.py",
             "deployable_dynamics.py", "delivery_contract.py", "delivery_checkpointing.py",
             "fits/servo-response-20260829.json"]
    for package in ("simple_dog_task", "simple_dog_task_v2", "simple_dog_task_current",
                    "simple_dog_task_current_body_v4", "simple_dog_task_current_body_v20"):
        files += [p.relative_to(root).as_posix() for p in (root / package).rglob("*") if p.suffix in (".py", ".yaml")]
    hashes = {}
    for name in files:
        source = root / name
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        hashes[name] = hashlib.sha256(target.read_bytes()).hexdigest()
    inputs = {}
    for key in ("SIMPLE_DOG_CONTROL_PROFILE", "SIMPLE_DOG_SIMULATION_FIT", "SIMPLE_DOG_CHECKPOINT"):
        if os.environ.get(key):
            path = Path(os.environ[key])
            inputs[key] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    profile = json.loads(Path(os.environ["SIMPLE_DOG_CONTROL_PROFILE"]).read_text())
    asset = Path(profile["robot"]["asset_usd"])
    inputs["asset"] = {"path": str(asset), "sha256": hashlib.sha256(asset.read_bytes()).hexdigest()}
    manifest = asset.with_suffix(".manifest.json")
    if manifest.exists():
        conversion = json.loads(manifest.read_text())
        for name, digest in conversion["source_layers"].items():
            if hashlib.sha256(Path(name).read_bytes()).hexdigest() != digest:
                raise ValueError(f"collision conversion source changed: {name}")
        if inputs["asset"]["sha256"] != conversion["output_sha256"]:
            raise ValueError("collision conversion output changed")
        inputs["asset"]["conversion_manifest"] = conversion
        layers = conversion["source_layers"]
    else:
        # The original SDF asset keeps its mesh layers below its own directory.
        # Hash those too: the root USD alone does not identify its geometry.
        layers = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in asset.parent.rglob("*") if p.suffix.lower() in (".usd", ".usda", ".usdc")}
    inputs["asset"]["source_layers"] = layers
    execution_environment = {name: os.environ.get(name) for name in
                             ("OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "MKL_NUM_THREADS")}
    (run / "source_manifest.json").write_text(json.dumps(
        dict(source_files=hashes, inputs=inputs, execution_environment=execution_environment), indent=2) + "\n")


if __name__ == "__main__":
    snapshot(Path(sys.argv[1]), Path(sys.argv[2]))
