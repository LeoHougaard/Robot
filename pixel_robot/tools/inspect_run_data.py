#!/usr/bin/env python3
"""Validate and summarize a Pixel Robot JSONL run without third-party packages."""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from run_data_source import open_run_text


def _numbers(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    return [float(item) for item in value if isinstance(item, (int, float))]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _stats(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "minimum": min(values, default=None),
        "median": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
        "maximum": max(values, default=None),
    }


def _idle_servo_telemetry(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("type") != "robot_rx":
            continue
        data = record.get("data", {})
        message = data.get("message", {}) if isinstance(data, dict) else {}
        if message.get("type") != "servo_telemetry" or not isinstance(message.get("id"), int):
            continue
        grouped.setdefault(message["id"], []).append(message)
    numeric_fields = (
        "position_raw", "position_deg", "joint_angle_deg", "speed_raw", "speed_rpm",
        "load_raw", "load_percent", "voltage_v", "temperature_c", "servo_status",
        "packet_status", "current_raw", "current_ma", "estimated_torque_kg_cm",
        "estimated_torque_nm",
    )
    return {
        str(servo_id): {
            "name": samples[-1].get("name"),
            "sample_count": len(samples),
            "measurements": {
                field: _stats([
                    float(sample[field])
                    for sample in samples
                    if isinstance(sample.get(field), (int, float))
                ])
                for field in numeric_fields
            },
        }
        for servo_id, samples in sorted(grouped.items())
    }


def summarize_run(path: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    with open_run_text(path) as (stream, source_label):
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"line {line_number}: invalid JSON: {error}") from error
            if not isinstance(record, dict):
                raise ValueError(f"line {line_number}: record is not a JSON object")
            records.append(record)

    if not records:
        raise ValueError("run file is empty")
    if records[0].get("type") != "session_start":
        raise ValueError("first record is not session_start")

    session_id = records[0].get("session_id")
    for expected_index, record in enumerate(records):
        if record.get("record_index") != expected_index:
            raise ValueError(
                f"record {expected_index}: record_index is {record.get('record_index')!r}"
            )
        if record.get("session_id") != session_id:
            raise ValueError(f"record {expected_index}: session_id changed")

    counts = Counter(str(record.get("type")) for record in records)
    frames = [record for record in records if record.get("type") == "derived_policy_frame"]
    frame_data = [record.get("data", {}) for record in frames]
    frame_times = [
        int(record["host_monotonic_ns"])
        for record in frames
        if isinstance(record.get("host_monotonic_ns"), int)
    ]
    duration_seconds = (
        (int(records[-1]["host_monotonic_ns"]) - int(records[0]["host_monotonic_ns"]))
        / 1_000_000_000
        if isinstance(records[0].get("host_monotonic_ns"), int)
        and isinstance(records[-1].get("host_monotonic_ns"), int)
        else None
    )
    record_write_rate_hz = (
        (len(frame_times) - 1) / ((frame_times[-1] - frame_times[0]) / 1_000_000_000)
        if len(frame_times) >= 2 and frame_times[-1] > frame_times[0]
        else None
    )

    firmware_samples = [
        int(data["firmware_sample_ms"])
        for data in frame_data
        if isinstance(data, dict) and isinstance(data.get("firmware_sample_ms"), int)
    ]
    firmware_intervals = [
        float((later - earlier) & 0xFFFF_FFFF)
        for earlier, later in zip(firmware_samples, firmware_samples[1:])
        if 0 < ((later - earlier) & 0xFFFF_FFFF) <= 1_000
    ]
    frame_rate_hz = (
        1_000.0 / (sum(firmware_intervals) / len(firmware_intervals))
        if firmware_intervals
        else record_write_rate_hz
    )

    sequences = [
        int(data["command_sequence"])
        for data in frame_data
        if isinstance(data, dict) and isinstance(data.get("command_sequence"), int)
    ]
    missing_sequences = sum(
        max(0, current - previous - 1) for previous, current in zip(sequences, sequences[1:])
    )
    nonmonotonic_sequences = sum(
        current <= previous for previous, current in zip(sequences, sequences[1:])
    )
    feedback_ticks = [
        int(tick)
        for data in frame_data
        for tick in [
            data.get("input_feedback_tick", data.get("input_robot_state", {}).get("tick"))
            if isinstance(data, dict)
            else None
        ]
        if isinstance(tick, int)
    ]
    missing_feedback_ticks = sum(
        max(0, current - previous - 1)
        for previous, current in zip(feedback_ticks, feedback_ticks[1:])
    )
    nonmonotonic_feedback_ticks = sum(
        current <= previous for previous, current in zip(feedback_ticks, feedback_ticks[1:])
    )
    applied_sequences = [
        int(sequence)
        for data in frame_data
        for sequence in [
            data.get("input_state_sequence", data.get("input_robot_state", {}).get("seq"))
            if isinstance(data, dict)
            else None
        ]
        if isinstance(sequence, int)
    ]
    repeated_applied_sequences = sum(
        current == previous
        for previous, current in zip(applied_sequences, applied_sequences[1:])
    )
    command_to_feedback_ms = [
        float(data["command_to_feedback_ns"]) / 1_000_000.0
        for data in frame_data
        if isinstance(data, dict)
        and isinstance(data.get("command_to_feedback_ns"), (int, float))
    ]

    tracking = [
        float(data["tracking_error_deg"])
        for data in frame_data
        if isinstance(data, dict) and isinstance(data.get("tracking_error_deg"), (int, float))
    ]
    inference = [
        float(data["inference_ms"])
        for data in frame_data
        if isinstance(data, dict) and isinstance(data.get("inference_ms"), (int, float))
    ]

    pitches: list[float] = []
    voltages: list[float] = []
    temperatures: list[float] = []
    currents: list[float] = []
    loads: list[float] = []
    incomplete_feedback = 0
    incomplete_current = 0
    current_complete_frames = 0
    current_by_servo: dict[int, list[float]] = {}
    current_expected_by_servo: Counter[int] = Counter()
    current_us: list[float] = []
    feedback_us: list[float] = []
    firmware_frame_us: list[float] = []
    for data in frame_data:
        state = data.get("input_robot_state", {}) if isinstance(data, dict) else {}
        if not isinstance(state, dict):
            continue
        pitch = state.get("imu_pitch_deg")
        if isinstance(pitch, (int, float)):
            pitches.append(float(pitch))
        voltages.extend(value / 10.0 for value in _numbers(state.get("voltage_tenths")))
        temperatures.extend(_numbers(state.get("temperature_c")))
        currents.extend(abs(value) for value in _numbers(state.get("current_raw")))
        loads.extend(abs(value) for value in _numbers(state.get("load_raw")))
        if state.get("feedback_complete") is False:
            incomplete_feedback += 1
        if state.get("current_complete") is True:
            current_complete_frames += 1
        elif "current_complete" in state:
            incomplete_current += 1
        ids = state.get("ids")
        current_values = state.get("current_raw")
        if isinstance(ids, list) and isinstance(current_values, list):
            for servo_id, current in zip(ids, current_values):
                if not isinstance(servo_id, int):
                    continue
                current_expected_by_servo[servo_id] += 1
                if isinstance(current, (int, float)):
                    current_by_servo.setdefault(servo_id, []).append(float(current))
        for key, destination in (
            ("current_us", current_us),
            ("feedback_us", feedback_us),
            ("frame_us", firmware_frame_us),
        ):
            value = state.get(key)
            if isinstance(value, (int, float)):
                destination.append(float(value) / 1_000.0)

    servo_current = {}
    for servo_id in sorted(current_expected_by_servo):
        samples = current_by_servo.get(servo_id, [])
        expected = current_expected_by_servo[servo_id]
        servo_current[str(servo_id)] = {
            "sample_count": len(samples),
            "coverage_fraction": len(samples) / expected if expected else None,
            "minimum_ma": min(samples, default=None) * 6.5 if samples else None,
            "maximum_ma": max(samples, default=None) * 6.5 if samples else None,
            "maximum_abs_ma": max((abs(value) for value in samples), default=0.0) * 6.5
            if samples
            else None,
        }

    start_data = records[0].get("data", {})
    end_data = records[-1].get("data", {}) if records[-1].get("type") == "session_end" else {}
    gate_reasons: list[str] = []
    if len(feedback_ticks) < 2:
        gate_reasons.append("firmware feedback ticks are absent")
    if not firmware_intervals:
        gate_reasons.append("no valid firmware sample intervals")
    elif not 19.0 <= (_percentile(firmware_intervals, 0.5) or 0.0) <= 21.0:
        gate_reasons.append("median firmware interval is outside 19-21 ms")
    if (_percentile(firmware_intervals, 0.95) or float("inf")) > 25.0:
        gate_reasons.append("p95 firmware interval exceeds 25 ms")
    if missing_feedback_ticks:
        gate_reasons.append("firmware feedback ticks are missing")
    if nonmonotonic_feedback_ticks:
        gate_reasons.append("firmware feedback ticks are not strictly increasing")
    if incomplete_feedback:
        gate_reasons.append("critical servo feedback is incomplete")
    return {
        "file": source_label,
        "schema_version": start_data.get("schema_version") if isinstance(start_data, dict) else None,
        "session_id": session_id,
        "complete": records[-1].get("type") == "session_end",
        "outcome": end_data.get("outcome") if isinstance(end_data, dict) else None,
        "record_count": len(records),
        "record_types": dict(sorted(counts.items())),
        "duration_s": duration_seconds,
        "policy_frames": len(frames),
        "observed_policy_rate_hz": frame_rate_hz,
        "record_write_rate_hz": record_write_rate_hz,
        "firmware_sample_interval_ms": _stats(firmware_intervals),
        "missing_command_sequences": missing_sequences,
        "nonmonotonic_command_sequences": nonmonotonic_sequences,
        "missing_feedback_ticks": missing_feedback_ticks,
        "nonmonotonic_feedback_ticks": nonmonotonic_feedback_ticks,
        "repeated_applied_command_sequences": repeated_applied_sequences,
        "incomplete_feedback_frames": incomplete_feedback,
        "current_complete_frames": current_complete_frames,
        "incomplete_current_frames": incomplete_current,
        "firmware_feedback_read_ms": _stats(feedback_us),
        "firmware_current_read_ms": _stats(current_us),
        "firmware_frame_ms": _stats(firmware_frame_us),
        "command_to_feedback_ms": _stats(command_to_feedback_ms),
        "transport_50hz_gate": {
            "passed": not gate_reasons,
            "target_period_ms": 20,
            "reasons": gate_reasons,
        },
        "servo_current": servo_current,
        "idle_servo_telemetry": _idle_servo_telemetry(records),
        "max_abs_pitch_deg": max((abs(value) for value in pitches), default=None),
        "max_tracking_error_deg": max(tracking, default=None),
        "inference_median_ms": _percentile(inference, 0.5),
        "inference_p95_ms": _percentile(inference, 0.95),
        "minimum_servo_voltage_v": min(voltages, default=None),
        "maximum_servo_temperature_c": max(temperatures, default=None),
        "maximum_abs_servo_current_raw": max(currents, default=None),
        "maximum_abs_servo_load_raw": max(loads, default=None),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "run",
        type=Path,
        help="exported JSONL run or training-capture ZIP",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable summary JSON")
    args = parser.parse_args()
    try:
        summary = summarize_run(args.run)
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        for key, value in summary.items():
            print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
