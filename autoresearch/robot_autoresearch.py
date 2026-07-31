#!/usr/bin/env python3
"""Sequential Isaac Lab -> evaluation -> Qwen autoresearch supervisor.

This small host-side control plane deliberately owns Docker sequencing. The AI
model receives evidence and returns a bounded experiment decision; it never
receives Docker access or an unrestricted shell.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


HOST_ROOT = Path("/home/leo/isaac-workspace/projects")
CONTAINER_ROOT = Path("/workspace/projects")
AUTORESEARCH_ROOT = HOST_ROOT / "autoresearch"
TRAINING_ROOT = HOST_ROOT / "training"
LAB_CONTAINER = "isaac-lab-gb10"
PLAYBACK_CONTAINER = "isaac-lab-dog-stream"
ONSHAPE_CONTAINER = "isaac-sim-onshape"
QWEN_CONTAINER = "qwen36-vllm"
MODEL_URL = "http://127.0.0.1:8000/v1"

DEFAULTS: dict[str, float] = {
    "command_forward_min": 0.15,
    "command_forward_max": 0.30,
    "body_vel_reward_scale": 5.0,
    "velocity_tracking_std": 0.20,
    "yaw_rate_reward_scale": 2.0,
    "gait_reward_scale": 5.0,
    "feet_air_time_reward_scale": 10.0,
    "air_time_variance_penalty_scale": -1.0,
    "base_motion_penalty_scale": -2.0,
    "base_orientation_penalty_scale": -3.0,
    "action_smoothness_penalty_scale": -1.0,
    "foot_slip_penalty_scale": -2.0,
    "undesired_contact_penalty_scale": -1.0,
}
RANGES: dict[str, tuple[float, float]] = {
    "command_forward_min": (0.05, 0.40),
    "command_forward_max": (0.10, 0.50),
    "body_vel_reward_scale": (1.0, 10.0),
    "velocity_tracking_std": (0.05, 0.50),
    "yaw_rate_reward_scale": (0.25, 5.0),
    "gait_reward_scale": (0.0, 10.0),
    "feet_air_time_reward_scale": (0.0, 15.0),
    "air_time_variance_penalty_scale": (-4.0, 0.0),
    "base_motion_penalty_scale": (-6.0, 0.0),
    "base_orientation_penalty_scale": (-8.0, 0.0),
    "action_smoothness_penalty_scale": (-4.0, 0.0),
    "foot_slip_penalty_scale": (-6.0, 0.0),
    "undesired_contact_penalty_scale": (-5.0, 0.0),
}

STOP_REQUESTED = False


class SupervisorError(RuntimeError):
    pass


class BudgetComplete(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def run(
    command: list[str],
    *,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        check=False,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SupervisorError(f"Command failed ({command[0]}): {detail}")
    return result


def container_running(name: str) -> bool:
    result = run(
        ["docker", "inspect", "--format", "{{.State.Running}}", name],
        check=False,
        timeout=15,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def gpu_utilization_percent() -> int | None:
    """Return the GB10 GPU utilization, or None when telemetry is unavailable."""
    result = run(
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        return None
    try:
        return int(result.stdout.strip().splitlines()[0])
    except (IndexError, ValueError):
        return None


def gpu_compute_process_pids() -> list[int]:
    result = run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid",
            "--format=csv,noheader,nounits",
        ],
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        try:
            pids.append(int(line.strip()))
        except ValueError:
            continue
    return pids


def stop_exact_container(name: str) -> None:
    if container_running(name):
        run(["docker", "stop", "--time", "30", name], timeout=45)


def host_to_container(path: Path) -> str:
    return str(CONTAINER_ROOT / path.resolve().relative_to(HOST_ROOT))


def load_manifest(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "robot_id",
        "adapter",
        "checkpoint",
        "num_envs",
        "next_max_iterations",
        "iteration_increment",
        "max_cycles",
        "poll_seconds",
        "video_length",
        "qwen_model",
        "terrain",
        "max_minutes",
        "tuning",
    }
    missing = sorted(required - set(data))
    if missing:
        raise SupervisorError(f"Manifest is missing: {', '.join(missing)}")
    if data["schema_version"] != 1 or data["adapter"] != "simple_dog_v1":
        raise SupervisorError("Only schema 1 with adapter simple_dog_v1 is supported.")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", data["robot_id"]):
        raise SupervisorError("robot_id must use lowercase letters, digits, and hyphens.")
    checkpoint_pattern = (
        r"/workspace/projects/training/logs/rl_games/"
        r"simple_dog(_rough)?_velocity_direct/[A-Za-z0-9_./-]+\.pth"
    )
    if not re.fullmatch(checkpoint_pattern, data["checkpoint"]):
        raise SupervisorError("Manifest checkpoint is outside the Simple Dog logs.")
    baseline_checkpoint = data.get("baseline_checkpoint", data["checkpoint"])
    if not re.fullmatch(checkpoint_pattern, baseline_checkpoint):
        raise SupervisorError(
            "Manifest baseline_checkpoint is outside the Simple Dog logs."
        )
    data["baseline_checkpoint"] = baseline_checkpoint
    if data["terrain"] not in {"flat", "rough"}:
        raise SupervisorError("terrain must be flat or rough.")
    if not isinstance(data["max_minutes"], int) or not 5 <= data["max_minutes"] <= 1440:
        raise SupervisorError(
            "max_minutes (PPO training minutes) must be an integer in [5, 1440]."
        )
    for key, low, high in (
        ("num_envs", 128, 16384),
        ("next_max_iterations", 1, 100000),
        ("iteration_increment", 0, 5000),
        ("max_cycles", 1, 100),
        ("poll_seconds", 5, 300),
        ("video_length", 100, 3000),
    ):
        if not isinstance(data[key], int) or not low <= data[key] <= high:
            raise SupervisorError(f"{key} must be an integer in [{low}, {high}].")
    chunks = data.get("training_chunks_per_cycle", 1)
    if not isinstance(chunks, int) or not 1 <= chunks <= 50:
        raise SupervisorError(
            "training_chunks_per_cycle must be an integer in [1, 50]."
        )
    data["training_chunks_per_cycle"] = chunks
    evaluation_candidates = data.get("evaluation_candidates_per_cycle", 1)
    if (
        not isinstance(evaluation_candidates, int)
        or not 1 <= evaluation_candidates <= min(chunks, 10)
    ):
        raise SupervisorError(
            "evaluation_candidates_per_cycle must be an integer in "
            f"[1, {min(chunks, 10)}]."
        )
    data["evaluation_candidates_per_cycle"] = evaluation_candidates
    data["tuning"] = validate_full_tuning(data["tuning"])
    return data


def checkpoint_training_reward(checkpoint: Path) -> float | None:
    """Extract RL-Games' terminal episodic reward from a bounded checkpoint."""
    match = re.search(r"_rew__(-?[0-9]+(?:\.[0-9]+)?)_\.pth$", checkpoint.name)
    return float(match.group(1)) if match else None


def rank_training_candidates(
    candidates: list[dict[str, Any]], limit: int
) -> list[dict[str, Any]]:
    """Shortlist the strongest same-config blocks without favoring the final one."""
    return sorted(
        candidates,
        key=lambda item: (
            item["training_reward"] is not None,
            (
                float(item["training_reward"])
                if item["training_reward"] is not None
                else float("-inf")
            ),
            int(item["chunk"]),
        ),
        reverse=True,
    )[:limit]


def validate_full_tuning(raw: object) -> dict[str, float]:
    if not isinstance(raw, dict):
        raise SupervisorError("tuning must be an object.")
    unknown = sorted(set(raw) - set(RANGES))
    if unknown:
        raise SupervisorError(f"Unknown tuning keys: {', '.join(unknown)}")
    result: dict[str, float] = {}
    for key, value in raw.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SupervisorError(f"Tuning value {key} must be numeric.")
        numeric = float(value)
        low, high = RANGES[key]
        if not low <= numeric <= high:
            raise SupervisorError(f"Tuning value {key} is outside [{low}, {high}].")
        result[key] = numeric
    merged = DEFAULTS | result
    if merged["command_forward_min"] > merged["command_forward_max"]:
        raise SupervisorError("command_forward_min exceeds command_forward_max.")
    return result


def scalar_last(metrics: dict[str, Any], suffix: str, default: float) -> float:
    for key, record in metrics.items():
        if key.endswith(suffix) and isinstance(record, dict):
            value = record.get("last")
            if isinstance(value, (int, float)):
                return float(value)
    return default


def promotion_score(metrics: dict[str, Any]) -> float:
    survival = scalar_last(metrics, "mean_survival_fraction", 0.0)
    falls = scalar_last(metrics, "Episode_Termination/fell", 1.0)
    velocity = scalar_last(metrics, "mean_velocity_error", 10.0)
    lateral = scalar_last(metrics, "mean_body_lateral_speed", 10.0)
    heading = scalar_last(metrics, "mean_heading_error", 10.0)
    terrain_level = scalar_last(metrics, "terrain_level", 0.0)
    displacement = scalar_last(metrics, "forward_displacement", -10.0)
    swing = sum(
        scalar_last(metrics, f"swing_fraction_{foot}", 0.0)
        for foot in ("front_right", "front_left", "back_right", "back_left")
    ) / 4.0
    return (
        100.0 * survival
        - 100.0 * falls
        - 200.0 * velocity
        - 50.0 * lateral
        - 50.0 * heading
        + 20.0 * swing
        + 10.0 * terrain_level
        + 50.0 * displacement
    )


def is_meaningful_promotion(
    candidate: dict[str, Any], incumbent: dict[str, Any]
) -> bool:
    """Require visible task progress, not a tiny aggregate-score fluctuation."""
    candidate_displacement = scalar_last(
        candidate, "forward_displacement", -10.0
    )
    incumbent_displacement = scalar_last(
        incumbent, "forward_displacement", -10.0
    )
    candidate_survival = scalar_last(
        candidate, "mean_survival_fraction", 0.0
    )
    incumbent_survival = scalar_last(
        incumbent, "mean_survival_fraction", 0.0
    )
    swing = [
        scalar_last(candidate, f"swing_fraction_{foot}", 0.0)
        for foot in ("front_right", "front_left", "back_right", "back_left")
    ]
    return (
        promotion_score(candidate) >= promotion_score(incumbent) + 5.0
        and candidate_displacement >= max(0.25, incumbent_displacement + 0.10)
        and candidate_survival >= incumbent_survival - 0.02
        and all(0.10 <= value <= 0.90 for value in swing)
    )


def parse_play_metrics(console: Path, terrain_level: float = 3.0) -> dict[str, Any]:
    """Build comparable policy metrics from a fixed-command playback rollout."""
    rows: list[dict[str, str]] = []
    for line in console.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("PLAY_METRICS "):
            continue
        row: dict[str, str] = {}
        for token in line.removeprefix("PLAY_METRICS ").split():
            if "=" in token:
                key, value = token.split("=", 1)
                row[key] = value
        rows.append(row)
    if not rows:
        raise SupervisorError("Playback produced no physical rollout metrics.")

    def mean_value(key: str, *, absolute: bool = False) -> float:
        values = [float(row[key]) for row in rows if key in row]
        if not values:
            raise SupervisorError(f"Playback metric is missing: {key}")
        if absolute:
            values = [abs(value) for value in values]
        return sum(values) / len(values)

    velocity_errors = [
        abs(float(row["command_forward"]) - float(row["body_forward"]))
        for row in rows
        if "command_forward" in row and "body_forward" in row
    ]
    if not velocity_errors:
        raise SupervisorError("Playback velocity metrics are missing.")
    swing = [float(value) for value in rows[-1]["swing_fraction_frflbrbl"].split(",")]
    if len(swing) != 4:
        raise SupervisorError("Playback did not report four foot swing fractions.")
    displacement = float(rows[-1].get("forward_displacement", "0"))
    return {
        "RoughRollout/mean_survival_fraction": {"last": 1.0},
        "RoughRollout/Episode_Termination/fell": {"last": 0.0},
        "RoughRollout/mean_velocity_error": {
            "last": sum(velocity_errors) / len(velocity_errors)
        },
        "RoughRollout/mean_body_lateral_speed": {
            "last": mean_value("body_lateral", absolute=True)
        },
        "RoughRollout/mean_heading_error": {
            "last": max(0.0, 1.0 - mean_value("heading_alignment"))
        },
        "RoughRollout/terrain_level": {"last": terrain_level},
        "RoughRollout/swing_fraction_front_right": {"last": swing[0]},
        "RoughRollout/swing_fraction_front_left": {"last": swing[1]},
        "RoughRollout/swing_fraction_back_right": {"last": swing[2]},
        "RoughRollout/swing_fraction_back_left": {"last": swing[3]},
        "RoughRollout/forward_displacement": {"last": displacement},
        "RoughRollout/sample_count": {"last": len(rows)},
    }


def extract_json(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    decoder = json.JSONDecoder()
    for position, character in enumerate(text):
        if character != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[position:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise SupervisorError("Qwen did not return a JSON decision object.")


def bounded_tuning(
    current: dict[str, float], proposed: object
) -> tuple[dict[str, float], list[str]]:
    if not isinstance(proposed, dict):
        return current.copy(), ["tuning_changes was not an object"]
    accepted = current.copy()
    notes: list[str] = []
    changes = 0
    for key, value in proposed.items():
        if changes >= 2:
            notes.append(f"rejected {key}: at most two changes per cycle")
            continue
        if key not in RANGES or isinstance(value, bool) or not isinstance(
            value, (int, float)
        ):
            notes.append(f"rejected {key}: unknown or non-numeric")
            continue
        numeric = float(value)
        low, high = RANGES[key]
        base = (DEFAULTS | current)[key]
        max_delta = max(abs(base) * 0.25, 0.01)
        if not low <= numeric <= high:
            notes.append(f"rejected {key}: outside safe range")
            continue
        if abs(numeric - base) > max_delta:
            notes.append(f"rejected {key}: exceeds 25% per-cycle limit")
            continue
        if numeric != base:
            accepted[key] = numeric
            changes += 1
    validate_full_tuning(accepted)
    return accepted, notes


class Supervisor:
    def __init__(self, manifest_path: Path, dry_run: bool) -> None:
        self.manifest_path = manifest_path.resolve()
        self.manifest = load_manifest(self.manifest_path)
        self.dry_run = dry_run
        self.run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.run_dir = (
            AUTORESEARCH_ROOT
            / "runs"
            / self.manifest["robot_id"]
            / self.run_id
        )
        self.global_status = AUTORESEARCH_ROOT / "current_status.json"
        self.training_budget_seconds = 60.0 * self.manifest["max_minutes"]
        self.state: dict[str, Any] = {
            "run_id": self.run_id,
            "robot_id": self.manifest["robot_id"],
            "manifest": str(self.manifest_path),
            "supervisor_pid": os.getpid(),
            "phase": "initializing",
            "status": "running",
            "started_at": utc_now(),
            "cycle": 0,
            "checkpoint": self.manifest["baseline_checkpoint"],
            "training_checkpoint": self.manifest["checkpoint"],
            "best_score": None,
            "training_budget_minutes": self.manifest["max_minutes"],
            "training_seconds_completed": 0.0,
            "tuning": self.manifest["tuning"],
            "owned_container": None,
        }

    def update(self, phase: str, **extra: Any) -> None:
        self.state["phase"] = phase
        self.state["updated_at"] = utc_now()
        self.state.update(extra)
        atomic_json(self.run_dir / "status.json", self.state)
        atomic_json(self.global_status, self.state)

    def check_stop(self) -> None:
        if STOP_REQUESTED:
            raise SupervisorError("Stop requested.")

    def training_budget_reached(self, active_since: float | None = None) -> bool:
        elapsed = float(self.state.get("training_seconds_completed", 0.0))
        if active_since is not None:
            elapsed += time.monotonic() - active_since
        return elapsed >= self.training_budget_seconds

    def require_free_gpu(self) -> None:
        blockers = [
            name
            for name in (
                LAB_CONTAINER,
                PLAYBACK_CONTAINER,
                ONSHAPE_CONTAINER,
                QWEN_CONTAINER,
            )
            if container_running(name)
        ]
        if blockers:
            raise SupervisorError(
                "External GPU workload is active: " + ", ".join(blockers)
            )

    def start_lab(self) -> None:
        self.require_free_gpu()
        stopped_at = getattr(self, "_lab_stopped_at", None)
        if stopped_at is not None:
            # Match the startup sequence that has completed continuous PPO on
            # this GB10. A synthetic CUDA allocation can clear nvidia-smi's
            # stale utilization reading but leaves the following PhysX reset
            # less reliable, so the supervisor deliberately does not probe or
            # reset the GPU between Isaac processes.
            remaining = 10.0 - (time.monotonic() - stopped_at)
            if remaining > 0.0:
                time.sleep(remaining)
        if not container_running(LAB_CONTAINER):
            run(["docker", "start", LAB_CONTAINER], timeout=30)
        self.state["owned_container"] = LAB_CONTAINER
        # Kit leaves these process-local rendezvous artifacts behind when the
        # persistent container is stopped.  They contain no project data, and
        # stale copies have repeatedly wedged the following app startup.
        run(
            [
                "docker",
                "exec",
                LAB_CONTAINER,
                "/bin/bash",
                "-lc",
                "find /tmp -maxdepth 1 -type d -name 'carb.*' -empty -delete; "
                "rm -f /tmp/hub-leo.lock",
            ],
            timeout=30,
        )
        run(
            [
                "docker",
                "exec",
                LAB_CONTAINER,
                "/bin/bash",
                "/workspace/projects/training/ensure_simple_dog_meshes.sh",
            ],
            timeout=600,
        )

    def stop_owned(self) -> None:
        owned = self.state.get("owned_container")
        if owned in {LAB_CONTAINER, QWEN_CONTAINER}:
            stop_exact_container(owned)
            if owned == LAB_CONTAINER:
                self._lab_stopped_at = time.monotonic()
        self.state["owned_container"] = None

    def start_training(
        self, checkpoint: str, target: int, tuning_path: Path
    ) -> Path:
        helper = TRAINING_ROOT / "simple-dog-gb10.sh"
        output = run(
            [
                str(helper),
                "start",
                str(self.manifest["num_envs"]),
                str(target),
                checkpoint,
                host_to_container(tuning_path),
                self.manifest["terrain"],
            ],
            timeout=45,
        ).stdout
        paths = [
            Path(line.strip())
            for line in output.splitlines()
            if line.strip().startswith(str(TRAINING_ROOT / "runs" / "simple_dog"))
        ]
        if not paths:
            raise SupervisorError("Training launcher did not return a run directory.")
        return paths[-1]

    def prepare_training_continuation(
        self, source: Path, destination: Path
    ) -> None:
        run(
            [
                "docker",
                "exec",
                LAB_CONTAINER,
                "/workspace/isaaclab/isaaclab.sh",
                "-p",
                "/workspace/projects/training/"
                "prepare_rough_continuation_checkpoint.py",
                host_to_container(source),
                host_to_container(destination),
                "--learning-rate",
                "2.5e-5",
                "--reset-epoch",
            ],
            timeout=120,
        )
        if not destination.is_file() or destination.stat().st_size == 0:
            raise SupervisorError(
                "Prepared continuation checkpoint was not created."
            )

    def wait_training(
        self,
        training_run: Path,
        active_since: float,
        no_progress_timeout_seconds: float = 120.0,
    ) -> None:
        status_file = training_run / "status"
        console_file = training_run / "console.log"
        last_console_size = -1
        last_progress = time.monotonic()
        while True:
            self.check_stop()
            status = (
                status_file.read_text(encoding="utf-8").strip()
                if status_file.exists()
                else "starting"
            )
            self.update("policy_improvement", training_status=status)
            if status == "complete":
                return
            if status in {"failed", "interrupted"}:
                raise SupervisorError(f"Training ended with status {status}.")
            if not container_running(LAB_CONTAINER):
                raise SupervisorError("Isaac Lab stopped while training was active.")
            console_size = (
                console_file.stat().st_size if console_file.exists() else 0
            )
            if console_size != last_console_size:
                last_console_size = console_size
                last_progress = time.monotonic()
            elif time.monotonic() - last_progress >= no_progress_timeout_seconds:
                raise SupervisorError(
                    "Training made no console progress for "
                    f"{int(no_progress_timeout_seconds)} seconds."
                )
            time.sleep(self.manifest["poll_seconds"])

    @staticmethod
    def completed_training_seconds(training_run: Path) -> float:
        console = (training_run / "console.log").read_text(
            encoding="utf-8", errors="replace"
        )
        matches = re.findall(r"Training time:\s*([0-9]+(?:\.[0-9]+)?)\s*seconds", console)
        if not matches:
            raise SupervisorError(
                "Completed training log did not report actual PPO training time."
            )
        return float(matches[-1])

    def run_training_chunk(
        self,
        checkpoint: str,
        target: int,
        tuning_path: Path,
        max_attempts: int = 6,
    ) -> tuple[Path, float]:
        """Run one bounded PPO epoch, retrying only a detected launch stall."""
        for attempt in range(1, max_attempts + 1):
            training_run = self.start_training(checkpoint, target, tuning_path)
            self.update(
                "policy_improvement",
                training_run=str(training_run),
                training_attempt=attempt,
                training_attempts_allowed=max_attempts,
            )
            training_started = time.monotonic()
            try:
                self.wait_training(training_run, training_started)
                return training_run, self.completed_training_seconds(training_run)
            except SupervisorError as exc:
                if (
                    "made no console progress" not in str(exc)
                    or attempt >= max_attempts
                ):
                    raise
                # A wedged Kit bootstrap consumes no PPO samples. Recreate only
                # the exact reusable Lab runtime, preserve every checkpoint,
                # and retry the same target without charging training budget.
                self.update(
                    "policy_improvement_retry",
                    training_retry_reason=str(exc),
                )
                self.stop_owned()
                self.start_lab()
        raise SupervisorError("Training retry loop exited unexpectedly.")

    @staticmethod
    def experiment_dir(training_run: Path) -> Path:
        console = training_run / "console.log"
        match = re.findall(
            r"Exact experiment name requested from command line: "
            r"(/workspace/projects/[^\r\n]+)",
            console.read_text(encoding="utf-8", errors="replace"),
        )
        if not match:
            raise SupervisorError("Training log did not identify its experiment.")
        container_path = Path(match[-1])
        return HOST_ROOT / container_path.relative_to(CONTAINER_ROOT)

    @staticmethod
    def inspect_metrics(experiment: Path) -> dict[str, Any]:
        metrics_result = run(
            [
                "docker",
                "exec",
                LAB_CONTAINER,
                "/workspace/isaaclab/isaaclab.sh",
                "-p",
                "/workspace/projects/training/inspect_simple_dog_run.py",
                "--json",
                host_to_container(experiment),
            ],
            timeout=120,
        )
        try:
            return extract_json(metrics_result.stdout)
        except SupervisorError as exc:
            raise SupervisorError("Metric inspector did not return JSON.") from exc

    def run_rollout_attempt(
        self,
        checkpoint: Path,
        experiment: Path,
        no_progress_timeout_seconds: float = 120.0,
    ) -> None:
        """Render one rollout while detecting a wedged Kit bootstrap."""
        command = [
            "docker",
            "exec",
            LAB_CONTAINER,
            "/bin/bash",
            "/workspace/projects/training/render_simple_dog_playback.sh",
            host_to_container(checkpoint),
            str(self.manifest["video_length"]),
            self.manifest["terrain"],
        ]
        process = subprocess.Popen(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        console_file = experiment / "visual_validation" / "console.log"
        last_console_size = -1
        last_progress = time.monotonic()
        started = last_progress
        while True:
            if STOP_REQUESTED:
                self.stop_owned()
                process.wait(timeout=30)
                raise SupervisorError("Stop requested.")
            return_code = process.poll()
            if return_code is not None:
                stdout, stderr = process.communicate()
                if return_code != 0:
                    detail = (stderr or stdout).strip()
                    raise SupervisorError(f"Command failed (docker): {detail}")
                return
            console_size = (
                console_file.stat().st_size if console_file.exists() else 0
            )
            if console_size != last_console_size:
                last_console_size = console_size
                last_progress = time.monotonic()
            elif (
                time.monotonic() - last_progress
                >= no_progress_timeout_seconds
            ):
                self.stop_owned()
                process.wait(timeout=30)
                raise SupervisorError(
                    "Rollout made no console progress for "
                    f"{int(no_progress_timeout_seconds)} seconds."
                )
            if time.monotonic() - started >= 900.0:
                self.stop_owned()
                process.wait(timeout=30)
                raise SupervisorError("Rollout exceeded 900 seconds.")
            time.sleep(5)

    def run_rollout_with_retries(
        self,
        checkpoint: Path,
        experiment: Path,
        max_attempts: int = 6,
    ) -> None:
        for attempt in range(1, max_attempts + 1):
            self.update(
                "rollout",
                rollout_attempt=attempt,
                rollout_attempts_allowed=max_attempts,
            )
            try:
                self.run_rollout_attempt(checkpoint, experiment)
                return
            except SupervisorError as exc:
                if (
                    "made no console progress" not in str(exc)
                    or attempt >= max_attempts
                ):
                    raise
                self.update(
                    "rollout_retry",
                    rollout_retry_reason=str(exc),
                )
                self.start_lab()
        raise SupervisorError("Rollout retry loop exited unexpectedly.")

    def render_rollout(
        self, checkpoint: Path, experiment: Path, artifact_name: str
    ) -> tuple[dict[str, Any], Path]:
        container_checkpoint = host_to_container(checkpoint)
        self.update("rollout", candidate_checkpoint=container_checkpoint)
        self.run_rollout_with_retries(checkpoint, experiment)
        rollout_metrics = parse_play_metrics(
            experiment / "visual_validation" / "console.log",
            terrain_level=3.0 if self.manifest["terrain"] == "rough" else 0.0,
        )
        atomic_json(self.run_dir / f"{artifact_name}-rollout-metrics.json", rollout_metrics)
        videos = sorted(
            experiment.rglob("*.mp4"),
            key=lambda item: item.stat().st_mtime,
        )
        video = videos[-1] if videos else None
        if video is None:
            raise SupervisorError("Rollout did not produce a video file.")
        saved_video = self.run_dir / f"{artifact_name}-rollout.mp4"
        shutil.copy2(video, saved_video)
        return rollout_metrics, saved_video

    def evaluate(
        self,
        experiment: Path,
        checkpoint: Path | None = None,
        artifact_name: str | None = None,
    ) -> tuple[Path, dict[str, Any], dict[str, Any], Path]:
        if checkpoint is None:
            policy_name = (
                "simple_dog_rough_velocity_direct.pth"
                if self.manifest["terrain"] == "rough"
                else "simple_dog_velocity_direct.pth"
            )
            checkpoint = self.latest_training_checkpoint(experiment, policy_name)
        artifact_name = artifact_name or f"cycle-{self.state['cycle']}"
        rollout_metrics, video = self.render_rollout(
            checkpoint, experiment, artifact_name
        )
        metrics = self.inspect_metrics(experiment)
        video_check = run(
            [
                "docker",
                "exec",
                LAB_CONTAINER,
                "/workspace/isaaclab/isaaclab.sh",
                "-p",
                "/workspace/projects/training/validate_rollout_video.py",
                host_to_container(video),
            ],
            check=False,
            timeout=120,
        )
        try:
            visual_health = extract_json(video_check.stdout)
        except SupervisorError as exc:
            raise SupervisorError("Rollout video health check returned no JSON.") from exc
        atomic_json(
            self.run_dir / f"{artifact_name}-video-health.json",
            visual_health,
        )
        if video_check.returncode != 0 or not visual_health.get("valid"):
            raise SupervisorError(
                "Rollout video is not usable visual evidence: "
                + str(visual_health.get("reason", "unknown video failure"))
            )
        contact_sheet = self.run_dir / f"{artifact_name}-contact-sheet.jpg"
        run(
            [
                "docker",
                "exec",
                LAB_CONTAINER,
                "/workspace/isaaclab/isaaclab.sh",
                "-p",
                "/workspace/projects/training/make_rollout_contact_sheet.py",
                host_to_container(video),
                host_to_container(contact_sheet),
            ],
            timeout=120,
        )
        if not contact_sheet.is_file() or contact_sheet.stat().st_size == 0:
            raise SupervisorError("Rollout contact sheet was not created.")
        atomic_json(self.run_dir / f"{artifact_name}-metrics.json", metrics)
        return checkpoint, metrics, rollout_metrics, video

    @staticmethod
    def latest_training_checkpoint(experiment: Path, policy_name: str) -> Path:
        """Return the newest policy produced by a bounded training process."""
        candidates = sorted(
            (experiment / "nn").glob("*.pth"),
            key=lambda item: item.stat().st_mtime,
        )
        if not candidates:
            raise SupervisorError(
                f"Candidate checkpoint is missing below: {experiment / 'nn'}"
            )
        last_checkpoints = [
            item for item in candidates if item.name.startswith("last_")
        ]
        if last_checkpoints:
            return last_checkpoints[-1]
        preferred = experiment / "nn" / policy_name
        return preferred if preferred.is_file() else candidates[-1]

    def start_qwen(self) -> None:
        if container_running(LAB_CONTAINER):
            raise SupervisorError("Refusing to start Qwen while Isaac Lab is running.")
        if not container_running(QWEN_CONTAINER):
            run(["docker", "start", QWEN_CONTAINER], timeout=30)
        self.state["owned_container"] = QWEN_CONTAINER
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            self.check_stop()
            try:
                with urllib.request.urlopen(MODEL_URL + "/models", timeout=5) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError, OSError):
                pass
            if not container_running(QWEN_CONTAINER):
                raise SupervisorError("Qwen stopped before its API became ready.")
            time.sleep(5)
        raise SupervisorError("Qwen API was not ready within 15 minutes.")

    def ask_qwen(
        self, metrics: dict[str, Any], video: Path | None
    ) -> tuple[dict[str, Any], str]:
        prompt_path = AUTORESEARCH_ROOT / "prompts" / "evolution.md"
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    prompt_path.read_text(encoding="utf-8")
                    + "\n\nCurrent tuning:\n"
                    + json.dumps(DEFAULTS | self.state["tuning"], indent=2)
                    + "\n\nTraining metrics:\n"
                    + json.dumps(metrics, indent=2)
                ),
            }
        ]
        contact_sheet = (
            video.with_name(video.name.replace("-rollout.mp4", "-contact-sheet.jpg"))
            if video
            else None
        )
        if (
            contact_sheet
            and contact_sheet.is_file()
            and contact_sheet.stat().st_size <= 25 * 1024 * 1024
        ):
            encoded = base64.b64encode(contact_sheet.read_bytes()).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": "data:image/jpeg;base64," + encoded},
                }
            )
        else:
            content[0]["text"] += (
                "\n\nNo rollout contact sheet was available within the 25 MiB input limit."
            )
        payload = {
            "model": self.manifest["qwen_model"],
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 4096,
            "temperature": 0.1,
            "response_format": {"type": "json_object"},
            # Qwen3 reasoning can consume the whole output budget and leave
            # content empty. Decisions need concise machine-readable JSON.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        request = urllib.request.Request(
            MODEL_URL + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                body = json.load(response)
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            json.JSONDecodeError,
        ) as exc:
            raise SupervisorError(f"Qwen analysis request failed: {exc}") from exc
        message = body["choices"][0]["message"]
        raw_value = (
            message.get("content")
            or message.get("reasoning_content")
            or message.get("reasoning")
            or ""
        )
        if isinstance(raw_value, str):
            raw = raw_value
        elif isinstance(raw_value, dict):
            raw = json.dumps(raw_value)
        elif isinstance(raw_value, list):
            raw = "\n".join(
                str(item.get("text", ""))
                if isinstance(item, dict)
                else str(item)
                for item in raw_value
            )
        else:
            raw = str(raw_value)
        atomic_json(
            self.run_dir / f"cycle-{self.state['cycle']}-qwen.json",
            {"raw": raw, "message": message},
        )
        decision = extract_json(raw)
        atomic_json(
            self.run_dir / f"cycle-{self.state['cycle']}-qwen.json",
            {"decision": decision, "raw": raw, "message": message},
        )
        return decision, raw

    def write_escalation(self, reason: str, decision: dict[str, Any] | None) -> None:
        request = self.run_dir / "codex_request.md"
        request.write_text(
            "# Codex escalation request\n\n"
            f"- Robot: `{self.manifest['robot_id']}`\n"
            f"- Run: `{self.run_id}`\n"
            f"- Cycle: `{self.state['cycle']}`\n"
            f"- Checkpoint: `{self.state['checkpoint']}`\n"
            f"- Reason: {reason}\n\n"
            "## Qwen decision\n\n"
            f"```json\n{json.dumps(decision, indent=2)}\n```\n",
            encoding="utf-8",
        )
        self.update(
            "escalation",
            status="needs_codex",
            error=reason,
            codex_request=str(request),
        )

    def write_advisory(self, reason: str, decision: dict[str, Any] | None) -> None:
        """Persist Qwen concerns without ending a duration-guaranteed run."""
        cycle = int(self.state["cycle"])
        request = self.run_dir / f"cycle-{cycle}-codex-advisory.md"
        request.write_text(
            "# Deferred Codex advisory\n\n"
            f"- Robot: `{self.manifest['robot_id']}`\n"
            f"- Run: `{self.run_id}`\n"
            f"- Cycle: `{cycle}`\n"
            f"- Preserved best: `{self.state['checkpoint']}`\n"
            f"- Reason: {reason}\n\n"
            "Training continues from the preserved best checkpoint until the "
            "requested PPO training-time budget is fulfilled.\n\n"
            "## Qwen decision\n\n"
            f"```json\n{json.dumps(decision, indent=2)}\n```\n",
            encoding="utf-8",
        )
        atomic_json(
            self.run_dir / f"cycle-{cycle}-advisory.json",
            {"reason": reason, "decision": decision, "created_at": utc_now()},
        )
        self.state["latest_advisory"] = str(request)
        self.state["latest_advisory_reason"] = reason

    def archive_best(self, checkpoint: str, score: float, cycle: int) -> None:
        source = HOST_ROOT / Path(checkpoint).relative_to(CONTAINER_ROOT)
        if not source.is_file():
            raise SupervisorError(f"Best checkpoint cannot be archived: {source}")
        archive = self.run_dir / "best_checkpoint.pth"
        temporary = archive.with_suffix(".pth.tmp")
        shutil.copy2(source, temporary)
        os.replace(temporary, archive)
        atomic_json(
            self.run_dir / "best-checkpoint.json",
            {
                "source_checkpoint": checkpoint,
                "archive": str(archive),
                "score": score,
                "cycle": cycle,
                "updated_at": utc_now(),
            },
        )
        self.state["best_checkpoint_archive"] = str(archive)

    def run_cycles(self) -> None:
        if os.geteuid() != 1001 or os.environ.get("USER") not in {None, "leo"}:
            raise SupervisorError("Supervisor must run as GB10 user leo (UID 1001).")
        self.run_dir.mkdir(parents=True, exist_ok=False)
        (self.run_dir / "supervisor.pid").write_text(str(os.getpid()), encoding="ascii")
        shutil.copy2(self.manifest_path, self.run_dir / "manifest.json")
        self.update("preflight")
        self.require_free_gpu()
        if self.dry_run:
            self.update("dry_run_complete", status="complete")
            return

        checkpoint = self.state["checkpoint"]
        training_checkpoint = self.state["training_checkpoint"]
        target = int(self.manifest["next_max_iterations"])
        current_tuning = dict(self.state["tuning"])
        best_score: float | None = None
        best_rollout: dict[str, Any] | None = None
        cycle = 0

        while not self.training_budget_reached():
            cycle += 1
            self.check_stop()
            self.state["cycle"] = cycle
            tuning_path = self.run_dir / f"cycle-{cycle}-tuning.json"
            atomic_json(tuning_path, current_tuning)

            self.update("environment", target_max_iterations=target)
            self.start_lab()
            if best_score is None:
                source_checkpoint = Path(checkpoint)
                source_experiment = (
                    HOST_ROOT
                    / source_checkpoint.relative_to(CONTAINER_ROOT)
                ).parent.parent
                source_metrics = self.inspect_metrics(source_experiment)
                atomic_json(self.run_dir / "baseline-metrics.json", source_metrics)
                baseline_rollout, _ = self.render_rollout(
                    HOST_ROOT / source_checkpoint.relative_to(CONTAINER_ROOT),
                    source_experiment,
                    "baseline",
                )
                best_score = promotion_score(baseline_rollout)
                best_rollout = baseline_rollout
                self.archive_best(checkpoint, best_score, 0)
                self.update(
                    "baseline_evaluated",
                    best_score=best_score,
                    best_rollout=best_rollout,
                )
                # Rendering and PPO are separate Kit workloads.  Recreate the
                # runtime boundary before the first trainer just as we do
                # between later chunks; otherwise the trainer can inherit
                # renderer process state and wedge during app startup.
                self.stop_owned()
                self.start_lab()
            cycle_training_seconds = 0.0
            experiment: Path | None = None
            cycle_candidates: list[dict[str, Any]] = []
            for chunk in range(int(self.manifest["training_chunks_per_cycle"])):
                self.update(
                    "policy_improvement",
                    training_chunk=chunk + 1,
                    training_chunks_per_cycle=self.manifest[
                        "training_chunks_per_cycle"
                    ],
                )
                training_run, chunk_seconds = self.run_training_chunk(
                    training_checkpoint, target, tuning_path
                )
                cycle_training_seconds += chunk_seconds
                self.state["training_seconds_completed"] = float(
                    self.state.get("training_seconds_completed", 0.0)
                ) + chunk_seconds
                self.update(
                    "policy_improvement",
                    cycle_training_seconds=cycle_training_seconds,
                    training_minutes_completed=(
                        self.state["training_seconds_completed"] / 60.0
                    ),
                )
                experiment = self.experiment_dir(training_run)
                policy_name = (
                    "simple_dog_rough_velocity_direct.pth"
                    if self.manifest["terrain"] == "rough"
                    else "simple_dog_velocity_direct.pth"
                )
                latest_checkpoint = self.latest_training_checkpoint(
                    experiment, policy_name
                )
                prepared_checkpoint = (
                    experiment
                    / "nn"
                    / f"prepared_after_target_{target}.pth"
                )
                self.prepare_training_continuation(
                    latest_checkpoint, prepared_checkpoint
                )
                cycle_candidates.append(
                    {
                        "chunk": chunk + 1,
                        "checkpoint": str(latest_checkpoint),
                        "prepared_checkpoint": str(prepared_checkpoint),
                        "training_reward": checkpoint_training_reward(
                            latest_checkpoint
                        ),
                        "training_seconds": chunk_seconds,
                    }
                )
                training_checkpoint = host_to_container(prepared_checkpoint)
                target += int(self.manifest["iteration_increment"])
                self.update(
                    "policy_improvement",
                    training_checkpoint=training_checkpoint,
                    target_max_iterations=target,
                    cycle_candidates=cycle_candidates,
                )
                if chunk + 1 < int(
                    self.manifest["training_chunks_per_cycle"]
                ):
                    self.stop_owned()
                    self.start_lab()
            if experiment is None:
                raise SupervisorError("Training cycle produced no experiment.")
            shortlist = rank_training_candidates(
                cycle_candidates,
                int(self.manifest["evaluation_candidates_per_cycle"]),
            )
            if not shortlist:
                raise SupervisorError("Training cycle produced no candidates.")
            # Isaac/Kit can leave process-lifetime renderer state behind after
            # many sequential headless trainers in one long-lived container.
            # Recreate only the runtime process boundary before visual
            # validation; the bind-mounted checkpoints and caches persist.
            self.stop_owned()
            self.start_lab()
            evaluations: list[dict[str, Any]] = []
            for index, record in enumerate(shortlist):
                if index:
                    self.stop_owned()
                    self.start_lab()
                candidate_path = Path(str(record["checkpoint"]))
                candidate_experiment = candidate_path.parent.parent
                artifact_name = (
                    f"cycle-{cycle}-candidate-{int(record['chunk'])}"
                )
                evaluated, candidate_metrics, candidate_rollout, candidate_video = (
                    self.evaluate(
                        candidate_experiment,
                        checkpoint=candidate_path,
                        artifact_name=artifact_name,
                    )
                )
                evaluations.append(
                    {
                        **record,
                        "checkpoint": str(evaluated),
                        "score": promotion_score(candidate_rollout),
                        "metrics": candidate_metrics,
                        "rollout_metrics": candidate_rollout,
                        "video": str(candidate_video),
                    }
                )
                self.update(
                    "rollout",
                    candidate_evaluations=evaluations,
                    evaluation_candidate=index + 1,
                    evaluation_candidates=len(shortlist),
                )

            eligible = [
                item
                for item in evaluations
                if best_rollout is None
                or is_meaningful_promotion(
                    item["rollout_metrics"], best_rollout
                )
            ]
            selected = max(
                eligible or evaluations,
                key=lambda item: float(item["score"]),
            )
            candidate = Path(str(selected["checkpoint"]))
            metrics = selected["metrics"]
            rollout_metrics = selected["rollout_metrics"]
            video = Path(str(selected["video"]))
            score = float(selected["score"])
            candidate_container = host_to_container(candidate)
            training_checkpoint = host_to_container(
                Path(str(selected["prepared_checkpoint"]))
            )
            promoted = bool(eligible)
            if promoted:
                checkpoint = candidate_container
                best_score = score
                best_rollout = rollout_metrics
                self.archive_best(checkpoint, best_score, cycle)
            self.update(
                "rollout_complete",
                candidate_checkpoint=candidate_container,
                candidate_score=score,
                promoted=promoted,
                candidate_evaluations=evaluations,
                checkpoint=checkpoint,
                training_checkpoint=training_checkpoint,
                best_score=best_score,
                best_rollout=best_rollout,
                video=str(video) if video else None,
            )

            self.stop_owned()
            decision: dict[str, Any] | None = None
            action = "skipped"
            notes: list[str] = []
            if cycle <= int(self.manifest["max_cycles"]):
                self.update("evolution")
                try:
                    self.start_qwen()
                    decision, _ = self.ask_qwen(
                        {
                            "rough_rollout": rollout_metrics,
                            "training_diagnostics": metrics,
                        },
                        video,
                    )
                except SupervisorError as exc:
                    action = "analysis_failure"
                    self.write_advisory(str(exc), None)
                finally:
                    self.stop_owned()

                if decision is not None:
                    action = str(decision.get("action"))
                    if action == "continue":
                        current_tuning, notes = bounded_tuning(
                            current_tuning, decision.get("tuning_changes", {})
                        )
                    elif action in {"hold", "escalate"}:
                        self.write_advisory(
                            str(
                                decision.get("codex_request")
                                or decision.get("summary")
                                or f"Qwen requested {action}."
                            ),
                            decision,
                        )
                    else:
                        self.write_advisory(
                            "Qwen returned an invalid action.", decision
                        )
            else:
                notes.append(
                    "Qwen analysis cap reached; continuing deterministic "
                    "training/evaluation from the preserved best."
                )
            self.state["tuning"] = current_tuning
            self.update(
                "cycle_complete",
                status="running",
                qwen_action=action,
                qwen_summary=decision.get("summary", "") if decision else "",
                tuning_validation_notes=notes,
            )

        self.update("training_budget_complete", status="complete")

    def execute(self) -> int:
        try:
            self.run_cycles()
            if self.state.get("status") == "needs_codex":
                return 5
            return 0
        except SupervisorError as exc:
            message = str(exc)
            if "External GPU workload is active" in message:
                self.update("paused_external_workload", status="paused", error=message)
                return 3
            if message == "Stop requested.":
                self.update("stopped", status="stopped")
                return 130
            self.write_escalation(message, None)
            return 1
        except BudgetComplete:
            self.update(
                "training_budget_complete",
                status="complete",
                training_minutes_completed=(
                    float(self.state.get("training_seconds_completed", 0.0)) / 60.0
                ),
            )
            return 0
        except Exception as exc:  # noqa: BLE001 - persist unexpected controller faults
            self.write_escalation(
                f"Unexpected supervisor error: {type(exc).__name__}: {exc}",
                None,
            )
            return 1
        finally:
            try:
                self.stop_owned()
            except Exception as exc:  # noqa: BLE001 - cleanup must preserve main error
                self.state["cleanup_error"] = str(exc)
                atomic_json(self.global_status, self.state)


def handle_signal(_number: int, _frame: object) -> None:
    global STOP_REQUESTED
    STOP_REQUESTED = True


def main() -> int:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--manifest", type=Path)
    source.add_argument("--queue", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    AUTORESEARCH_ROOT.mkdir(parents=True, exist_ok=True)
    lock_path = AUTORESEARCH_ROOT / "controller.lock"
    with lock_path.open("w", encoding="ascii") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another robot autoresearch supervisor is active.", file=sys.stderr)
            return 4
        signal.signal(signal.SIGTERM, handle_signal)
        signal.signal(signal.SIGINT, handle_signal)
        if args.manifest:
            manifests = [args.manifest]
        else:
            queue_path = args.queue.resolve()
            queue = json.loads(queue_path.read_text(encoding="utf-8"))
            if queue.get("schema_version") != 1 or not isinstance(
                queue.get("manifests"), list
            ):
                print("Queue must use schema 1 and contain a manifests list.", file=sys.stderr)
                return 2
            names = queue["manifests"]
            if not names or len(names) != len(set(names)):
                print("Queue manifests must be nonempty and unique.", file=sys.stderr)
                return 2
            manifests = []
            for name in names:
                if not isinstance(name, str) or not re.fullmatch(
                    r"[a-z0-9][a-z0-9-]{0,62}\.json", name
                ):
                    print(f"Invalid queue manifest name: {name}", file=sys.stderr)
                    return 2
                manifests.append(AUTORESEARCH_ROOT / "robots" / name)
        for manifest in manifests:
            result = Supervisor(manifest, args.dry_run).execute()
            if result != 0:
                return result
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
