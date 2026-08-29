#!/usr/bin/env python3
"""Create a provenance-preserving policy-only slice of a training capture."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path, PurePosixPath
import time
from typing import Any
import uuid
import zipfile

from run_data_source import open_run_text, verify_training_capture


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_records(source: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    with open_run_text(source, manifest) as (stream, _):
        records = [json.loads(line) for line in stream if line.strip()]
    if not records or records[0].get("type") != "session_start":
        raise ValueError("capture does not contain a valid run")
    return records


def _jsonl(records: list[dict[str, Any]]) -> bytes:
    return ("".join(
        json.dumps(record, separators=(",", ":"), allow_nan=False) + "\n"
        for record in records
    )).encode("utf-8")


def trim_records(
    records: list[dict[str, Any]],
    *,
    source_capture_sha256: str,
    source_run_sha256: str,
    start_seconds: float,
    end_seconds: float,
    reason: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if start_seconds < 0 or end_seconds <= start_seconds:
        raise ValueError("trim interval must satisfy 0 <= start < end")
    frames = [record for record in records if record.get("type") == "derived_policy_frame"]
    if len(frames) < 2:
        raise ValueError("capture has fewer than two policy frames")
    first_frame_ns = int(frames[0]["host_monotonic_ns"])
    requested_start_ns = first_frame_ns + round(start_seconds * 1_000_000_000)
    requested_end_ns = first_frame_ns + round(end_seconds * 1_000_000_000)
    kept_frames = [
        record
        for record in frames
        if requested_start_ns <= int(record["host_monotonic_ns"]) < requested_end_ns
    ]
    if len(kept_frames) < 2:
        raise ValueError("trim interval contains fewer than two policy frames")
    first_kept_ns = int(kept_frames[0]["host_monotonic_ns"])
    last_kept_ns = int(kept_frames[-1]["host_monotonic_ns"])
    source_session_id = str(records[0].get("session_id", "unknown"))
    trim_key = (
        f"{source_session_id}:{source_capture_sha256}:"
        f"{first_kept_ns}:{last_kept_ns}"
    )
    session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, trim_key))
    provenance = {
        "schema_version": 1,
        "selection_method": "policy_time_half_open_interval",
        "reason": reason,
        "source_session_id": source_session_id,
        "source_capture_sha256": source_capture_sha256,
        "source_run_sha256": source_run_sha256,
        "requested_start_seconds": start_seconds,
        "requested_end_seconds": end_seconds,
        "actual_start_seconds": (first_kept_ns - first_frame_ns) / 1_000_000_000,
        "actual_end_seconds": (last_kept_ns - first_frame_ns) / 1_000_000_000,
        "source_policy_frames": len(frames),
        "retained_policy_frames": len(kept_frames),
    }

    start = copy.deepcopy(records[0])
    start["session_id"] = session_id
    start["host_monotonic_ns"] = first_kept_ns
    start["host_unix_ms"] = int(kept_frames[0]["host_unix_ms"])
    start.setdefault("data", {}).setdefault("context", {})["trim"] = provenance
    trimmed = [start]
    trimmed.extend(
        copy.deepcopy(record)
        for record in records
        if record.get("type") not in {"session_start", "session_end"}
        and isinstance(record.get("host_monotonic_ns"), int)
        and first_kept_ns <= int(record["host_monotonic_ns"]) <= last_kept_ns
    )
    trimmed.append({
        "type": "session_end",
        "session_id": session_id,
        "record_index": -1,
        "host_unix_ms": int(kept_frames[-1]["host_unix_ms"]),
        "host_monotonic_ns": last_kept_ns,
        "data": {
            "outcome": "trimmed_complete_run",
            "detail": reason,
            "trim": provenance,
        },
    })
    for index, record in enumerate(trimmed):
        record["session_id"] = session_id
        record["record_index"] = index
    return trimmed, provenance


def trim_capture(
    source: Path,
    output_capture: Path,
    output_run: Path,
    *,
    start_seconds: float,
    end_seconds: float,
    reason: str,
) -> dict[str, Any]:
    manifest = verify_training_capture(source)
    source_capture_sha256 = _digest(source.read_bytes())
    with zipfile.ZipFile(source) as archive:
        source_run = archive.read(manifest["run_entry"])
        retained = {
            name: archive.read(name)
            for name in archive.namelist()
            if name not in {"manifest.json", manifest["run_entry"]}
        }
    records = _load_records(source, manifest)
    trimmed, provenance = trim_records(
        records,
        source_capture_sha256=source_capture_sha256,
        source_run_sha256=_digest(source_run),
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        reason=reason,
    )
    run_data = _jsonl(trimmed)
    output_run.parent.mkdir(parents=True, exist_ok=True)
    output_run.write_bytes(run_data)

    run_entry = f"run/{PurePosixPath(output_run.name).name}"
    entries = {run_entry: run_data, **retained}
    new_manifest = copy.deepcopy(manifest)
    new_manifest["created_unix_ms"] = int(time.time() * 1000)
    new_manifest["run_entry"] = run_entry
    new_manifest.setdefault("context", {})["trim"] = provenance
    new_manifest["files"] = [
        {"path": name, "size_bytes": len(data), "sha256": _digest(data)}
        for name, data in entries.items()
    ]
    output_capture.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_capture, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
        archive.writestr(
            "manifest.json",
            json.dumps(new_manifest, separators=(",", ":"), allow_nan=False) + "\n",
        )
    verify_training_capture(output_capture)
    return {
        "source": str(source),
        "output_capture": str(output_capture),
        "output_run": str(output_run),
        "output_capture_sha256": _digest(output_capture.read_bytes()),
        "output_run_sha256": _digest(run_data),
        "trim": provenance,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="source training-capture ZIP")
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--end-seconds", type=float, required=True)
    parser.add_argument("--output-capture", type=Path, required=True)
    parser.add_argument("--output-run", type=Path, required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    try:
        result = trim_capture(
            args.source,
            args.output_capture,
            args.output_run,
            start_seconds=args.start_seconds,
            end_seconds=args.end_seconds,
            reason=args.reason,
        )
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
