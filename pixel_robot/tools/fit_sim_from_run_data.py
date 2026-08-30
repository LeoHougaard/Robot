#!/usr/bin/env python3
"""Fit reproducible simulation timing and actuator evidence from Pixel run JSONL."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Any, Iterable

from run_data_source import open_run_text, verify_training_capture


REPORT_SCHEMA_VERSION = 2
MIN_VALID_SAMPLE_INTERVAL_MS = 5
MAX_VALID_SAMPLE_INTERVAL_MS = 100
DEFAULT_MAX_LAG_FRAMES = 20
IDENTIFIABILITY_WARNINGS = [
    "Closed-loop gait data cannot separate motor dynamics from linkage load, ground contact, body motion, or controller response.",
    "Lag fits are effective target-to-feedback phase delays, not isolated motor time constants.",
    "The logs contain no foot contact, force, external body pose, ground-reaction force, or synchronized video measurements.",
    "Policy-time battery readings may be held idle samples, so voltage-dependent actuator dynamics require runs at multiple measured live voltages.",
    "Do not infer mass, inertia, friction, or terrain contact parameters from these controller logs alone.",
]


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def _stats(values: Iterable[float], *, include_abs_p95: bool = False) -> dict[str, Any]:
    samples = list(values)
    result: dict[str, Any] = {
        "count": len(samples),
        "mean": statistics.fmean(samples) if samples else None,
        "median": statistics.median(samples) if samples else None,
        "p95": _percentile(samples, 0.95),
        "min": min(samples) if samples else None,
        "max": max(samples) if samples else None,
    }
    if include_abs_p95:
        result["p95_abs"] = _percentile([abs(value) for value in samples], 0.95)
        result["max_abs"] = max((abs(value) for value in samples), default=None)
    return result


def _median_absolute_difference(values: Iterable[float]) -> float | None:
    samples = list(values)
    if not samples:
        return None
    center = statistics.median(samples)
    return statistics.median(abs(value - center) for value in samples)


def _session_context_report(start_record: dict[str, Any]) -> dict[str, Any]:
    data = start_record.get("data", {})
    context = data.get("context", {}) if isinstance(data, dict) else {}
    calibration = context.get("calibration", {}) if isinstance(context, dict) else {}
    imu = calibration.get("imu", {}) if isinstance(calibration, dict) else {}
    link = context.get("robot_link", {}) if isinstance(context, dict) else {}
    voltage = link.get("servo_battery_voltage") if isinstance(link, dict) else None
    return {
        "control_hz": calibration.get("control_hz") if isinstance(calibration, dict) else None,
        "servo_battery_voltage_v": (
            float(voltage) if isinstance(voltage, (int, float)) else None
        ),
        "voltage_effect_identifiable": False,
        "gyro_bias_sensor_dps": (
            [float(value) for value in imu.get("gyro_bias_dps", [])]
            if isinstance(imu, dict)
            and isinstance(imu.get("gyro_bias_dps"), list)
            and all(isinstance(value, (int, float)) for value in imu["gyro_bias_dps"])
            else []
        ),
    }


def _parse_records(lines: Iterable[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"line {line_number}: invalid JSON: {error}") from error
        if not isinstance(record, dict):
            raise ValueError(f"line {line_number}: record is not an object")
        records.append(record)
    if not records:
        raise ValueError("run file is empty")
    if records[0].get("type") != "session_start":
        raise ValueError("first record is not session_start")
    session_id = records[0].get("session_id")
    for index, record in enumerate(records):
        if record.get("record_index") != index:
            raise ValueError(f"record {index}: unexpected record_index")
        if record.get("session_id") != session_id:
            raise ValueError(f"record {index}: session_id changed")
    return records


def _load_records(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return _parse_records(stream)


def _load_training_capture(path: Path) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    manifest = verify_training_capture(path)
    with open_run_text(path, manifest) as (stream, label):
        return label, _parse_records(stream), manifest


def _input_angles(frame: dict[str, Any]) -> dict[str, float]:
    state = frame.get("input_robot_state", {})
    ids = state.get("ids", [])
    angles = state.get("angles_deg", [])
    if not isinstance(ids, list) or not isinstance(angles, list) or len(ids) != len(angles):
        return {}
    return {
        str(servo_id): float(angle)
        for servo_id, angle in zip(ids, angles)
        if isinstance(servo_id, int) and isinstance(angle, (int, float))
    }


def _applied_targets(frame: dict[str, Any]) -> dict[str, Any]:
    matched = frame.get("input_applied_servo_target_deg")
    if isinstance(matched, dict):
        return matched
    targets = frame.get("servo_target_deg")
    return targets if isinstance(targets, dict) else {}


def _servo_metadata(start_record: dict[str, Any]) -> tuple[dict[str, str], list[float]]:
    data = start_record.get("data", {})
    context = data.get("context", {}) if isinstance(data, dict) else {}
    calibration = context.get("calibration", {}) if isinstance(context, dict) else {}
    joints = calibration.get("joints", []) if isinstance(calibration, dict) else []
    semantics = {
        str(joint["servo_id"]): str(joint.get("semantic", "unknown"))
        for joint in joints
        if isinstance(joint, dict) and isinstance(joint.get("servo_id"), int)
    }
    policy = context.get("policy_metadata", {}) if isinstance(context, dict) else {}
    contract = policy.get("action_contract", {}) if isinstance(policy, dict) else {}
    clips = contract.get("applied_normalized_clip_by_joint", []) if isinstance(contract, dict) else []
    limits = [abs(float(value)) for value in clips if isinstance(value, (int, float))]
    return semantics, limits


def _servo_report(
    frames: list[dict[str, Any]],
    semantics: dict[str, str],
    median_interval_ms: float | None,
    max_lag_frames: int,
) -> dict[str, Any]:
    servo_ids = sorted(
        {
            str(servo_id)
            for frame in frames
            for servo_id in (
                list(frame.get("servo_target_deg", {}))
                + list(frame.get("input_robot_state", {}).get("ids", []))
            )
        },
        key=lambda value: int(value),
    )
    errors = {servo_id: [] for servo_id in servo_ids}
    measured_speeds = {servo_id: [] for servo_id in servo_ids}
    target_speeds = {servo_id: [] for servo_id in servo_ids}
    current_raw = {servo_id: [] for servo_id in servo_ids}
    current_expected = {servo_id: 0 for servo_id in servo_ids}
    lag_squared_errors = {
        servo_id: {lag: [] for lag in range(max_lag_frames + 1)} for servo_id in servo_ids
    }
    lag_errors = {
        servo_id: {lag: [] for lag in range(max_lag_frames + 1)} for servo_id in servo_ids
    }
    has_sequence_matched_targets = any(
        isinstance(frame.get("input_applied_servo_target_deg"), dict) for frame in frames
    )

    for index, frame in enumerate(frames):
        measured = _input_angles(frame)
        state = frame.get("input_robot_state", {})
        ids = state.get("ids", []) if isinstance(state, dict) else []
        currents = state.get("current_raw", []) if isinstance(state, dict) else []
        if isinstance(ids, list) and isinstance(currents, list):
            for servo_id, current in zip(ids, currents):
                key = str(servo_id)
                if key not in current_expected:
                    continue
                current_expected[key] += 1
                if isinstance(current, (int, float)):
                    current_raw[key].append(float(current))
        for lag in range(max_lag_frames + 1):
            target_index = index - lag
            if target_index < 0:
                continue
            targets = _applied_targets(frames[target_index])
            for servo_id in servo_ids:
                target = targets.get(servo_id)
                angle = measured.get(servo_id)
                if isinstance(target, (int, float)) and angle is not None:
                    error = float(target) - angle
                    lag_errors[servo_id][lag].append(error)
                    lag_squared_errors[servo_id][lag].append(error ** 2)

    if has_sequence_matched_targets:
        for frame in frames:
            feedback = _input_angles(frame)
            for servo_id, target in _applied_targets(frame).items():
                if servo_id in feedback and isinstance(target, (int, float)):
                    errors.setdefault(servo_id, []).append(float(target) - feedback[servo_id])

    for index in range(len(frames) - 1):
        frame = frames[index]
        following = frames[index + 1]
        following_state = following.get("input_robot_state", {})
        command_sequence = frame.get("command_sequence")
        if not has_sequence_matched_targets and following_state.get("seq") == command_sequence:
            feedback = _input_angles(following)
            for servo_id, target in frame.get("servo_target_deg", {}).items():
                if servo_id in feedback and isinstance(target, (int, float)):
                    errors.setdefault(servo_id, []).append(float(target) - feedback[servo_id])

        current_state = frame.get("input_robot_state", {})
        current_sample = current_state.get("sample_ms")
        following_sample = following_state.get("sample_ms")
        if not isinstance(current_sample, int) or not isinstance(following_sample, int):
            continue
        sample_interval_ms = (following_sample - current_sample) & 0xFFFF_FFFF
        if not MIN_VALID_SAMPLE_INTERVAL_MS <= sample_interval_ms <= MAX_VALID_SAMPLE_INTERVAL_MS:
            continue
        seconds = sample_interval_ms / 1000.0
        current_angles = _input_angles(frame)
        following_angles = _input_angles(following)
        current_targets = _applied_targets(frame)
        following_targets = _applied_targets(following)
        for servo_id in servo_ids:
            if servo_id in current_angles and servo_id in following_angles:
                measured_speeds[servo_id].append(
                    abs(following_angles[servo_id] - current_angles[servo_id]) / seconds
                )
            current_target = current_targets.get(servo_id)
            following_target = following_targets.get(servo_id)
            if isinstance(current_target, (int, float)) and isinstance(
                following_target, (int, float)
            ):
                target_speeds[servo_id].append(
                    abs(float(following_target) - float(current_target)) / seconds
                )

    report: dict[str, Any] = {}
    for servo_id in servo_ids:
        servo_errors = errors[servo_id]
        absolute_errors = [abs(value) for value in servo_errors]
        rmse_by_lag: list[float | None] = []
        for lag in range(max_lag_frames + 1):
            squared = lag_squared_errors[servo_id][lag]
            rmse_by_lag.append(math.sqrt(statistics.fmean(squared)) if squared else None)
        valid_lags = [lag for lag, rmse in enumerate(rmse_by_lag) if rmse is not None]
        best_lag = min(valid_lags, key=lambda lag: (rmse_by_lag[lag], lag)) if valid_lags else None
        absolute_current_ma = [abs(value) * 6.5 for value in current_raw[servo_id]]
        current_differences_ma = [
            later - earlier
            for earlier, later in zip(absolute_current_ma, absolute_current_ma[1:])
        ]
        current_bias_ma = _percentile(absolute_current_ma, 0.20)
        current_working_max_ma = _percentile(absolute_current_ma, 0.95)
        current_scale_ma = (
            max(6.5, current_working_max_ma - current_bias_ma)
            if current_bias_ma is not None and current_working_max_ma is not None
            else None
        )
        current_clip_ma = max(absolute_current_ma) if absolute_current_ma else None
        current_clip_fraction = (
            sum(value >= current_clip_ma - 1.0e-9 for value in absolute_current_ma)
            / len(absolute_current_ma)
            if absolute_current_ma and current_clip_ma is not None
            else None
        )
        dropout_fraction = (
            1.0 - len(current_raw[servo_id]) / current_expected[servo_id]
            if current_expected[servo_id]
            else None
        )
        dropout_upper_95 = (
            3.0 / current_expected[servo_id] if current_expected[servo_id] else None
        )
        aligned_errors = (
            lag_errors[servo_id][best_lag] if best_lag is not None else []
        )
        report[servo_id] = {
            "semantic": semantics.get(servo_id, "unknown"),
            "current": {
                "coverage_fraction": (
                    len(current_raw[servo_id]) / current_expected[servo_id]
                    if current_expected[servo_id]
                    else None
                ),
                "raw": _stats(current_raw[servo_id], include_abs_p95=True),
                "abs_ma": _stats(absolute_current_ma),
                "simulation_fit": {
                    "normalization_bias_ma": current_bias_ma,
                    "normalization_scale_ma": current_scale_ma,
                    "working_p95_ma": current_working_max_ma,
                    "observed_clip_ma": current_clip_ma,
                    "observed_clip_fraction": current_clip_fraction,
                    # Consecutive differences include real load changes. Treat
                    # this robust value as an upper bound on white read noise.
                    "difference_mad_ma": _median_absolute_difference(
                        current_differences_ma
                    ),
                    "dropout_fraction": dropout_fraction,
                    # The rule of three avoids interpreting zero observed
                    # dropouts as proof of a perfect future transport.
                    "dropout_probability_upper_95": dropout_upper_95,
                    "missing_value_behavior": "hold_last_finite_and_validity_zero",
                },
            },
            "matched_feedback_frames": len(servo_errors),
            "error_deg": {
                "bias": statistics.fmean(servo_errors) if servo_errors else None,
                "mae": statistics.fmean(absolute_errors) if absolute_errors else None,
                "p95_abs": _percentile(absolute_errors, 0.95),
                "max_abs": max(absolute_errors) if absolute_errors else None,
            },
            "measured_speed_abs_deg_s": _stats(measured_speeds[servo_id]),
            "target_speed_abs_deg_s": _stats(target_speeds[servo_id]),
            "lag_fit": {
                "search_max_frames": max_lag_frames,
                "best_lag_frames": best_lag,
                "best_lag_ms": (
                    best_lag * median_interval_ms
                    if best_lag is not None and median_interval_ms is not None
                    else None
                ),
                "best_rmse_deg": rmse_by_lag[best_lag] if best_lag is not None else None,
                "aligned_bias_deg": (
                    statistics.fmean(aligned_errors) if aligned_errors else None
                ),
                "aligned_residual_mad_deg": _median_absolute_difference(aligned_errors),
                "rmse_deg_by_lag": rmse_by_lag,
            },
        }
    return report


def _action_report(frames: list[dict[str, Any]], limits: list[float]) -> dict[str, Any]:
    requested_by_joint: list[list[float]] = []
    applied_by_joint: list[list[float]] = []
    for frame in frames:
        requested = frame.get("requested_action", [])
        applied = frame.get("applied_action", [])
        if isinstance(requested, list):
            while len(requested_by_joint) < len(requested):
                requested_by_joint.append([])
            for index, value in enumerate(requested):
                if isinstance(value, (int, float)):
                    requested_by_joint[index].append(float(value))
        if isinstance(applied, list):
            while len(applied_by_joint) < len(applied):
                applied_by_joint.append([])
            for index, value in enumerate(applied):
                if isinstance(value, (int, float)):
                    applied_by_joint[index].append(float(value))
    requested_flat = [value for joint in requested_by_joint for value in joint]
    requested_fraction = (
        sum(abs(value) >= 0.999 for value in requested_flat) / len(requested_flat)
        if requested_flat
        else None
    )
    requested_by_joint_fraction = [
        sum(abs(value) >= 0.999 for value in joint) / len(joint) if joint else None
        for joint in requested_by_joint
    ]
    applied_by_joint_fraction: list[float | None] = []
    for index, joint in enumerate(applied_by_joint):
        limit = limits[index] if index < len(limits) else 1.0
        applied_by_joint_fraction.append(
            sum(abs(value) >= limit - 1e-4 for value in joint) / len(joint) if joint else None
        )
    return {
        "requested_at_actor_clip_fraction": requested_fraction,
        "requested_at_actor_clip_fraction_by_joint": requested_by_joint_fraction,
        "applied_at_safety_clip_fraction_by_joint": applied_by_joint_fraction,
        "applied_clip_by_joint": limits if limits else [1.0] * len(applied_by_joint),
    }


def _idle_servo_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        if record.get("type") != "robot_rx":
            continue
        data = record.get("data", {})
        message = data.get("message", {}) if isinstance(data, dict) else {}
        if message.get("type") != "servo_telemetry" or not isinstance(message.get("id"), int):
            continue
        grouped.setdefault(str(message["id"]), []).append(message)
    numeric_fields = (
        "position_raw", "position_deg", "joint_angle_deg", "speed_raw", "speed_rpm",
        "load_raw", "load_percent", "voltage_v", "temperature_c", "servo_status",
        "packet_status", "current_raw", "current_ma", "estimated_torque_kg_cm",
        "estimated_torque_nm",
    )
    return {
        servo_id: {
            "name": samples[-1].get("name"),
            "sample_count": len(samples),
            "measurements": {
                field: _stats(
                    float(sample[field])
                    for sample in samples
                    if isinstance(sample.get(field), (int, float))
                )
                for field in numeric_fields
            },
        }
        for servo_id, samples in sorted(grouped.items(), key=lambda item: int(item[0]))
    }


def _analyze_run(path: str | Path, records: list[dict[str, Any]], max_lag_frames: int) -> dict[str, Any]:
    frame_records = [record for record in records if record.get("type") == "derived_policy_frame"]
    frames = [record.get("data", {}) for record in frame_records]
    host_times_ms = [record["host_monotonic_ns"] / 1_000_000.0 for record in frame_records]
    host_intervals_ms = [later - earlier for earlier, later in zip(host_times_ms, host_times_ms[1:])]
    median_interval_ms = statistics.median(host_intervals_ms) if host_intervals_ms else None
    sample_intervals_ms: list[float] = []
    rejected_sample_intervals = 0
    for earlier, later in zip(frames, frames[1:]):
        first = earlier.get("firmware_sample_ms")
        second = later.get("firmware_sample_ms")
        if not isinstance(first, int) or not isinstance(second, int):
            continue
        interval = (second - first) & 0xFFFF_FFFF
        if MIN_VALID_SAMPLE_INTERVAL_MS <= interval <= MAX_VALID_SAMPLE_INTERVAL_MS:
            sample_intervals_ms.append(float(interval))
        else:
            rejected_sample_intervals += 1

    semantics, action_limits = _servo_metadata(records[0])
    states = [frame.get("input_robot_state", {}) for frame in frames]
    pitch = [float(state["imu_pitch_deg"]) for state in states if isinstance(state.get("imu_pitch_deg"), (int, float))]
    roll = [float(state["imu_roll_deg"]) for state in states if isinstance(state.get("imu_roll_deg"), (int, float))]
    accel_norm = [
        math.sqrt(sum(float(value) ** 2 for value in state["accel_mg"]))
        for state in states
        if isinstance(state.get("accel_mg"), list)
        and len(state["accel_mg"]) == 3
        and all(isinstance(value, (int, float)) for value in state["accel_mg"])
    ]
    gyro_vectors = [
        [float(value) for value in state["gyro_dps"]]
        for state in states
        if isinstance(state.get("gyro_dps"), list)
        and len(state["gyro_dps"]) == 3
        and all(isinstance(value, (int, float)) for value in state["gyro_dps"])
    ]
    gyro_peak = [max((abs(vector[axis]) for vector in gyro_vectors), default=None) for axis in range(3)]
    gyro_by_axis = [
        _stats(vector[axis] for vector in gyro_vectors) for axis in range(3)
    ]
    accel_vectors = [
        [float(value) for value in state["accel_mg"]]
        for state in states
        if isinstance(state.get("accel_mg"), list)
        and len(state["accel_mg"]) == 3
        and all(isinstance(value, (int, float)) for value in state["accel_mg"])
    ]
    accel_by_axis = [
        _stats(vector[axis] for vector in accel_vectors) for axis in range(3)
    ]
    gyro_body_vectors = [
        [float(value) for value in frame["gyro_body_rad_s"]]
        for frame in frames
        if isinstance(frame.get("gyro_body_rad_s"), list)
        and len(frame["gyro_body_rad_s"]) == 3
        and all(isinstance(value, (int, float)) for value in frame["gyro_body_rad_s"])
    ]
    gravity_vectors = [
        [float(value) for value in frame["projected_gravity_body"]]
        for frame in frames
        if isinstance(frame.get("projected_gravity_body"), list)
        and len(frame["projected_gravity_body"]) == 3
        and all(isinstance(value, (int, float)) for value in frame["projected_gravity_body"])
    ]

    sequences = [frame.get("command_sequence") for frame in frames]
    integer_sequences = [value for value in sequences if isinstance(value, int)]
    feedback_ticks = [
        frame.get("input_feedback_tick", frame.get("input_robot_state", {}).get("tick"))
        for frame in frames
    ]
    integer_feedback_ticks = [value for value in feedback_ticks if isinstance(value, int)]
    applied_sequences = [
        frame.get("input_state_sequence", frame.get("input_robot_state", {}).get("seq"))
        for frame in frames
    ]
    integer_applied_sequences = [value for value in applied_sequences if isinstance(value, int)]
    incomplete_feedback = sum(
        state.get("feedback_complete") is False for state in states if isinstance(state, dict)
    )
    current_complete = sum(
        state.get("current_complete") is True for state in states if isinstance(state, dict)
    )
    incomplete_current = sum(
        state.get("current_complete") is False for state in states if isinstance(state, dict)
    )
    missing_sequences = sum(
        max(0, current - previous - 1)
        for previous, current in zip(integer_sequences, integer_sequences[1:])
    )
    nonmonotonic_sequences = sum(
        current <= previous for previous, current in zip(integer_sequences, integer_sequences[1:])
    )
    missing_feedback_ticks = sum(
        max(0, current - previous - 1)
        for previous, current in zip(integer_feedback_ticks, integer_feedback_ticks[1:])
    )
    nonmonotonic_feedback_ticks = sum(
        current <= previous
        for previous, current in zip(integer_feedback_ticks, integer_feedback_ticks[1:])
    )
    repeated_applied_sequences = sum(
        current == previous
        for previous, current in zip(integer_applied_sequences, integer_applied_sequences[1:])
    )
    firmware_interval_p95 = _percentile(sample_intervals_ms, 0.95)
    transport_gate_reasons: list[str] = []
    if len(integer_feedback_ticks) < 2:
        transport_gate_reasons.append("firmware feedback ticks are absent")
    if not sample_intervals_ms:
        transport_gate_reasons.append("no valid firmware sample intervals")
    elif not 19.0 <= statistics.median(sample_intervals_ms) <= 21.0:
        transport_gate_reasons.append("median firmware interval is outside 19-21 ms")
    if firmware_interval_p95 is None or firmware_interval_p95 > 25.0:
        transport_gate_reasons.append("p95 firmware interval exceeds 25 ms")
    if missing_feedback_ticks:
        transport_gate_reasons.append("firmware feedback ticks are missing")
    if nonmonotonic_feedback_ticks:
        transport_gate_reasons.append("firmware feedback ticks are not strictly increasing")
    if incomplete_feedback:
        transport_gate_reasons.append("critical servo feedback is incomplete")
    end_data = records[-1].get("data", {})
    return {
        "file": str(path),
        "session_id": records[0].get("session_id"),
        "outcome": end_data.get("outcome") if isinstance(end_data, dict) else None,
        "frame_count": len(frames),
        "duration_s": (
            (host_times_ms[-1] - host_times_ms[0]) / 1000.0 if len(host_times_ms) >= 2 else 0.0
        ),
        "data_quality": {
            "missing_command_sequences": missing_sequences,
            "nonmonotonic_command_sequences": nonmonotonic_sequences,
            "missing_feedback_ticks": missing_feedback_ticks,
            "nonmonotonic_feedback_ticks": nonmonotonic_feedback_ticks,
            "repeated_applied_command_sequences": repeated_applied_sequences,
            "incomplete_feedback_frames": incomplete_feedback,
            "current_complete_frames": current_complete,
            "incomplete_current_frames": incomplete_current,
            "rejected_firmware_sample_intervals": rejected_sample_intervals,
        },
        "physical_context": _session_context_report(records[0]),
        "timing": {
            "observed_hz": (
                1000.0 / statistics.fmean(host_intervals_ms) if host_intervals_ms else None
            ),
            "host_frame_interval_ms": _stats(host_intervals_ms),
            "firmware_sample_interval_ms": _stats(sample_intervals_ms),
            "firmware_feedback_read_ms": _stats(
                float(state["feedback_us"]) / 1000.0
                for state in states[1:]
                if isinstance(state.get("feedback_us"), (int, float))
            ),
            "firmware_current_read_ms": _stats(
                float(state["current_us"]) / 1000.0
                for state in states
                if isinstance(state.get("current_us"), (int, float))
            ),
            "firmware_frame_ms": _stats(
                float(state["frame_us"]) / 1000.0
                for state in states[1:]
                if isinstance(state.get("frame_us"), (int, float))
            ),
            "android_frame_compute_ms": _stats(
                float(frame["frame_compute_ns"]) / 1_000_000.0
                for frame in frames
                if isinstance(frame.get("frame_compute_ns"), (int, float))
            ),
            "command_to_feedback_ms": _stats(
                float(frame["command_to_feedback_ns"]) / 1_000_000.0
                for frame in frames
                if isinstance(frame.get("command_to_feedback_ns"), (int, float))
            ),
            "inference_ms": _stats(
                float(frame["inference_ms"])
                for frame in frames
                if isinstance(frame.get("inference_ms"), (int, float))
            ),
        },
        "transport_50hz_gate": {
            "passed": not transport_gate_reasons,
            "target_period_ms": 20,
            "reasons": transport_gate_reasons,
        },
        "imu": {
            "pitch_deg": _stats(pitch, include_abs_p95=True),
            "roll_deg": _stats(roll, include_abs_p95=True),
            "accel_norm_mg": _stats(accel_norm),
            "gyro_abs_peak_dps_xyz": gyro_peak,
            "gyro_sensor_dps_xyz": gyro_by_axis,
            "acceleration_sensor_mg_xyz": accel_by_axis,
            "gyro_body_rad_s_xyz": [
                _stats(vector[axis] for vector in gyro_body_vectors)
                for axis in range(3)
            ],
            "projected_gravity_body_xyz": [
                _stats(vector[axis] for vector in gravity_vectors)
                for axis in range(3)
            ],
            "noise_identifiability": (
                "moving closed-loop data bounds sensor-plus-motion variation; "
                "it does not isolate stationary sensor noise"
            ),
        },
        "action_saturation": _action_report(frames, action_limits),
        "servos": _servo_report(frames, semantics, median_interval_ms, max_lag_frames),
        "idle_servo_telemetry": _idle_servo_report(records),
    }


def fit_path(source: Path, max_lag_frames: int = DEFAULT_MAX_LAG_FRAMES) -> dict[str, Any]:
    if max_lag_frames < 0:
        raise ValueError("max_lag_frames must be nonnegative")
    capture_manifest: dict[str, Any] | None = None
    preloaded: dict[str, list[dict[str, Any]]] = {}
    if source.is_file() and source.suffix.lower() == ".zip":
        label, records, capture_manifest = _load_training_capture(source)
        candidates = [label]
        preloaded[label] = records
    elif source.is_file():
        candidates = [str(source)]
    elif source.is_dir():
        candidates = [str(path) for path in sorted(source.glob("*.jsonl"))]
    else:
        raise ValueError(f"source does not exist: {source}")
    if not candidates:
        raise ValueError(f"no JSONL files found in {source}")

    selected: list[tuple[str, list[dict[str, Any]]]] = []
    excluded: list[dict[str, str]] = []
    for path in candidates:
        try:
            records = preloaded.get(path) or _load_records(Path(path))
        except (OSError, ValueError) as error:
            excluded.append({"file": str(path), "reason": f"invalid: {error}"})
            continue
        if records[-1].get("type") != "session_end":
            excluded.append({"file": str(path), "reason": "incomplete session"})
            continue
        frame_count = sum(record.get("type") == "derived_policy_frame" for record in records)
        if frame_count < 2:
            excluded.append({"file": str(path), "reason": "fewer than two policy frames"})
            continue
        selected.append((path, records))

    runs = [_analyze_run(path, records, max_lag_frames) for path, records in selected]
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "source": str(source),
        "training_capture": (
            {
                "verified": True,
                "schema_version": capture_manifest.get("schema_version"),
                "context": capture_manifest.get("context", {}),
                "files": capture_manifest.get("files", []),
            }
            if capture_manifest is not None
            else None
        ),
        "selection": {
            "candidate_file_count": len(candidates),
            "selected_complete_policy_run_count": len(runs),
            "selected_files": [str(path) for path, _ in selected],
            "excluded": excluded,
        },
        "runs": runs,
        "identifiability_warnings": IDENTIFIABILITY_WARNINGS,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "source",
        type=Path,
        help="one run JSONL, one Pixel training-capture ZIP, or a directory of run JSONL files",
    )
    parser.add_argument(
        "--max-lag-frames",
        type=int,
        default=DEFAULT_MAX_LAG_FRAMES,
        help=f"maximum target-to-feedback lag to fit (default: {DEFAULT_MAX_LAG_FRAMES})",
    )
    args = parser.parse_args()
    try:
        report = fit_path(args.source, args.max_lag_frames)
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    print(json.dumps(report, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
