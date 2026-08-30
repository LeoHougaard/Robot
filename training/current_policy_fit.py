"""Validated CurrentV3 simulation parameters derived from one Pixel fit report."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Iterable


EXPECTED_CAPTURE_SHA256 = (
    "b1389bf17ee37737674b0bb57c477ceb4b517eeb6038bdee88c77536fc799254"
)


@dataclass(frozen=True)
class CurrentPolicyFit:
    report_sha256: str
    capture_sha256: str
    frame_count: int
    duration_s: float
    observed_hz: float
    transport_50hz_passed: bool
    servo_ids: tuple[int, ...]
    semantics: tuple[str, ...]
    current_bias_ma: tuple[float, ...]
    current_scale_ma: tuple[float, ...]
    current_clip_ma: tuple[float, ...]
    current_noise_mad_ma: tuple[float, ...]
    empirical_dropout: tuple[float, ...]
    dropout_upper_95: tuple[float, ...]
    response_alpha: tuple[float, ...]
    speed_limit_rad_s: tuple[float, ...]
    residual_bias_rad: tuple[float, ...]
    residual_mad_rad: tuple[float, ...]
    command_delay_steps: tuple[int, int]
    current_delay_steps: tuple[int, int]
    gyro_bias_sensor_dps: tuple[float, ...]
    gyro_body_p95_rad_s: tuple[float, ...]
    projected_gravity_p95: tuple[float, ...]
    reference_voltage_v: float | None
    requested_actor_clip_fraction: float
    requested_actor_clip_fraction_by_joint: tuple[float, ...]
    applied_clip_fraction_by_joint: tuple[float, ...]

    def metadata(self) -> dict[str, Any]:
        """Return the immutable fit provenance embedded in run and export metadata."""
        return {
            "fit_schema_version": 2,
            "fit_sha256": self.report_sha256,
            "capture_sha256": self.capture_sha256,
            "frame_count": self.frame_count,
            "duration_s": self.duration_s,
            "observed_hz": self.observed_hz,
            "transport_50hz_passed": self.transport_50hz_passed,
            "servo_ids_in_policy_order": list(self.servo_ids),
            "semantics_in_policy_order": list(self.semantics),
            "current_normalization_bias_ma": list(self.current_bias_ma),
            "current_normalization_scale_ma": list(self.current_scale_ma),
            "current_observed_clip_ma": list(self.current_clip_ma),
            "current_noise_difference_mad_ma": list(self.current_noise_mad_ma),
            "empirical_dropout_fraction": list(self.empirical_dropout),
            "dropout_probability_upper_95": list(self.dropout_upper_95),
            "command_delay_steps": list(self.command_delay_steps),
            "current_delay_steps": list(self.current_delay_steps),
            "reference_voltage_v": self.reference_voltage_v,
            "voltage_effect_identifiable": False,
        }


def _finite(value: Any, label: str) -> float:
    if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label} must be finite")
    return float(value)


def _bounded_int_range(
    minimum_ms: Any,
    maximum_ms: Any,
    period_ms: float,
    *,
    maximum_steps: int,
) -> tuple[int, int]:
    minimum = max(0, round(_finite(minimum_ms, "delay minimum") / period_ms))
    maximum = max(minimum, math.ceil(_finite(maximum_ms, "delay maximum") / period_ms))
    return min(minimum, maximum_steps), min(maximum, maximum_steps)


def load_current_policy_fit(
    path: str | Path,
    policy_semantics: Iterable[str],
    *,
    capture_sha256: str = EXPECTED_CAPTURE_SHA256,
    control_hz: int = 50,
) -> CurrentPolicyFit:
    source = Path(path)
    payload = source.read_bytes()
    report = json.loads(payload)
    if report.get("report_schema_version") != 2:
        raise ValueError("CurrentV3 requires simulation fit schema 2")
    runs = report.get("runs")
    if not isinstance(runs, list) or len(runs) != 1:
        raise ValueError("CurrentV3 requires exactly one selected physical run")
    run = runs[0]
    if run.get("frame_count") != 2189:
        raise ValueError("CurrentV3 fit does not contain the 2,189-frame capture")
    if run.get("data_quality", {}).get("incomplete_feedback_frames") != 0:
        raise ValueError("CurrentV3 fit contains incomplete critical feedback")
    if run.get("data_quality", {}).get("current_complete_frames") != 2189:
        raise ValueError("CurrentV3 fit does not contain complete synchronized current")

    semantics = tuple(policy_semantics)
    servo_reports = run.get("servos", {})
    by_semantic = {
        servo.get("semantic"): (int(servo_id), servo)
        for servo_id, servo in servo_reports.items()
        if isinstance(servo, dict) and isinstance(servo.get("semantic"), str)
    }
    if set(by_semantic) != set(semantics) or len(semantics) != 12:
        raise ValueError("simulation fit semantics do not match the 12 policy joints")

    period_ms = 1000.0 / control_hz
    timing = run.get("timing", {})
    command_timing = timing.get("command_to_feedback_ms", {})
    current_timing = timing.get("firmware_current_read_ms", {})
    command_delay = _bounded_int_range(
        command_timing.get("min"),
        command_timing.get("p95"),
        period_ms,
        maximum_steps=3,
    )
    current_delay = _bounded_int_range(
        current_timing.get("min"),
        current_timing.get("p95"),
        period_ms,
        maximum_steps=2,
    )

    servo_ids: list[int] = []
    current_bias: list[float] = []
    current_scale: list[float] = []
    current_clip: list[float] = []
    current_noise: list[float] = []
    empirical_dropout: list[float] = []
    dropout_upper: list[float] = []
    response_alpha: list[float] = []
    speed_limit: list[float] = []
    residual_bias: list[float] = []
    residual_mad: list[float] = []
    for semantic in semantics:
        servo_id, servo = by_semantic[semantic]
        servo_ids.append(servo_id)
        current = servo.get("current", {}).get("simulation_fit", {})
        bias_ma = _finite(current.get("normalization_bias_ma"), f"{semantic} current bias")
        scale_ma = _finite(current.get("normalization_scale_ma"), f"{semantic} current scale")
        clip_ma = _finite(current.get("observed_clip_ma"), f"{semantic} current clip")
        if bias_ma < 0 or scale_ma <= 0 or clip_ma <= bias_ma:
            raise ValueError(f"{semantic} current normalization is invalid")
        current_bias.append(bias_ma)
        current_scale.append(scale_ma)
        current_clip.append(clip_ma)
        current_noise.append(max(0.0, _finite(current.get("difference_mad_ma"), f"{semantic} current noise")))
        empirical_dropout.append(
            max(0.0, _finite(current.get("dropout_fraction"), f"{semantic} dropout"))
        )
        dropout_upper.append(
            min(0.25, max(0.0, _finite(
                current.get("dropout_probability_upper_95"),
                f"{semantic} dropout upper bound",
            )))
        )

        lag = servo.get("lag_fit", {})
        lag_ms = _finite(lag.get("best_lag_ms"), f"{semantic} lag")
        command_delay_median_ms = _finite(
            command_timing.get(
                "median",
                0.5 * (
                    _finite(command_timing.get("min"), "command delay minimum")
                    + _finite(command_timing.get("p95"), "command delay p95")
                ),
            ),
            "command delay median",
        )
        dynamic_ms = max(period_ms, lag_ms - command_delay_median_ms)
        response_alpha.append(min(0.65, max(0.025, period_ms / dynamic_ms)))
        speed_deg_s = _finite(
            servo.get("measured_speed_abs_deg_s", {}).get("p95"),
            f"{semantic} speed",
        )
        speed_limit.append(math.radians(max(5.0, speed_deg_s)))
        residual_bias.append(math.radians(_finite(
            lag.get("aligned_bias_deg"), f"{semantic} residual bias"
        )))
        residual_mad.append(math.radians(max(0.0, _finite(
            lag.get("aligned_residual_mad_deg"), f"{semantic} residual error"
        ))))

    imu = run.get("imu", {})
    gyro_body = imu.get("gyro_body_rad_s_xyz", [])
    gravity = imu.get("projected_gravity_body_xyz", [])
    if len(gyro_body) != 3 or len(gravity) != 3:
        raise ValueError("simulation fit is missing deployable IMU distributions")
    physical = run.get("physical_context", {})
    saturation = run.get("action_saturation", {})
    return CurrentPolicyFit(
        report_sha256=hashlib.sha256(payload).hexdigest(),
        capture_sha256=capture_sha256.lower(),
        frame_count=int(run["frame_count"]),
        duration_s=_finite(run.get("duration_s"), "duration"),
        observed_hz=_finite(timing.get("observed_hz"), "observed rate"),
        transport_50hz_passed=bool(run.get("transport_50hz_gate", {}).get("passed")),
        servo_ids=tuple(servo_ids),
        semantics=semantics,
        current_bias_ma=tuple(current_bias),
        current_scale_ma=tuple(current_scale),
        current_clip_ma=tuple(current_clip),
        current_noise_mad_ma=tuple(current_noise),
        empirical_dropout=tuple(empirical_dropout),
        dropout_upper_95=tuple(dropout_upper),
        response_alpha=tuple(response_alpha),
        speed_limit_rad_s=tuple(speed_limit),
        residual_bias_rad=tuple(residual_bias),
        residual_mad_rad=tuple(residual_mad),
        command_delay_steps=command_delay,
        current_delay_steps=current_delay,
        gyro_bias_sensor_dps=tuple(
            _finite(value, "gyro bias")
            for value in physical.get("gyro_bias_sensor_dps", [])
        ),
        gyro_body_p95_rad_s=tuple(
            abs(_finite(axis.get("p95"), "body gyro p95")) for axis in gyro_body
        ),
        projected_gravity_p95=tuple(
            _finite(axis.get("p95"), "projected gravity p95") for axis in gravity
        ),
        reference_voltage_v=(
            _finite(physical["servo_battery_voltage_v"], "reference voltage")
            if physical.get("servo_battery_voltage_v") is not None
            else None
        ),
        requested_actor_clip_fraction=_finite(
            saturation.get("requested_at_actor_clip_fraction"), "actor clip fraction"
        ),
        requested_actor_clip_fraction_by_joint=tuple(
            _finite(value, "joint actor clip fraction")
            for value in saturation.get("requested_at_actor_clip_fraction_by_joint", [])
        ),
        applied_clip_fraction_by_joint=tuple(
            _finite(value, "joint applied clip fraction")
            for value in saturation.get("applied_at_safety_clip_fraction_by_joint", [])
        ),
    )
