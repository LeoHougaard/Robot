#!/usr/bin/env python3
"""Create dependency-free SVG graphs from a Pixel Robot JSONL recording."""

from __future__ import annotations

import argparse
import json
import math
from html import escape
from pathlib import Path
from typing import Any

from run_data_source import open_run_text


COLORS = [
    "#2563eb", "#dc2626", "#16a34a", "#9333ea", "#ea580c", "#0891b2",
    "#4f46e5", "#be123c", "#65a30d", "#7e22ce", "#d97706", "#0f766e",
]


def _load_frames(path: Path) -> list[dict[str, Any]]:
    frames: list[dict[str, Any]] = []
    with open_run_text(path) as (stream, _):
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"line {line_number}: invalid JSON: {error}") from error
            if record.get("type") == "derived_policy_frame" and isinstance(record.get("data"), dict):
                frames.append(record["data"])
    if len(frames) < 2:
        raise ValueError("run needs at least two derived_policy_frame records")
    return frames


def _elapsed_seconds(frames: list[dict[str, Any]]) -> list[float]:
    elapsed = [0.0]
    previous = frames[0].get("firmware_sample_ms")
    for frame in frames[1:]:
        current = frame.get("firmware_sample_ms")
        if isinstance(previous, int) and isinstance(current, int):
            interval = (current - previous) & 0xFFFF_FFFF
            elapsed.append(elapsed[-1] + interval / 1_000.0)
        else:
            elapsed.append(elapsed[-1] + 0.02)
        previous = current
    return elapsed


def _servo_series(
    frames: list[dict[str, Any]], key: str, scale: float = 1.0
) -> dict[int, list[float | None]]:
    servo_ids = sorted({
        servo_id
        for frame in frames
        for servo_id in frame.get("input_robot_state", {}).get("ids", [])
        if isinstance(servo_id, int)
    })
    result = {servo_id: [] for servo_id in servo_ids}
    for frame in frames:
        state = frame.get("input_robot_state", {})
        ids = state.get("ids", []) if isinstance(state, dict) else []
        values = state.get(key, []) if isinstance(state, dict) else []
        by_id = {
            servo_id: float(value) * scale
            for servo_id, value in zip(ids, values)
            if isinstance(servo_id, int) and isinstance(value, (int, float))
        }
        for servo_id in servo_ids:
            result[servo_id].append(by_id.get(servo_id))
    return result


def _target_series(frames: list[dict[str, Any]], servo_id: int) -> list[float | None]:
    return [
        float(value) if isinstance(value, (int, float)) else None
        for frame in frames
        for value in [
            frame.get("input_applied_servo_target_deg", frame.get("servo_target_deg", {}))
            .get(str(servo_id))
        ]
    ]


def _finite(values: list[float | None]) -> list[float]:
    return [value for value in values if isinstance(value, float) and math.isfinite(value)]


def _chart(
    path: Path,
    title: str,
    y_label: str,
    x: list[float],
    series: list[tuple[str, list[float | None], str]],
    reference_y: float | None = None,
) -> None:
    width, height = 1280, 680
    left, right, top, bottom = 90, 230, 62, 70
    plot_width = width - left - right
    plot_height = height - top - bottom
    values = [value for _, samples, _ in series for value in _finite(samples)]
    if reference_y is not None:
        values.append(reference_y)
    if not values:
        raise ValueError(f"no finite values for {title}")
    y_min, y_max = min(values), max(values)
    if y_min >= 0:
        y_min = 0.0
    if y_max <= 0:
        y_max = 0.0
    padding = max((y_max - y_min) * 0.08, 1.0)
    y_min -= padding
    y_max += padding
    x_max = max(x[-1], 0.001)

    def sx(value: float) -> float:
        return left + value / x_max * plot_width

    def sy(value: float) -> float:
        return top + (y_max - value) / (y_max - y_min) * plot_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        '<style>text{font-family:Arial,sans-serif;fill:#172033}.grid{stroke:#d8dee9;stroke-width:1}.axis{stroke:#334155;stroke-width:1.5}.label{font-size:14px}.title{font-size:24px;font-weight:700}.legend{font-size:13px}</style>',
        f'<text class="title" x="{left}" y="34">{escape(title)}</text>',
    ]
    for tick in range(6):
        x_value = x_max * tick / 5
        x_pixel = sx(x_value)
        parts.append(f'<line class="grid" x1="{x_pixel:.1f}" y1="{top}" x2="{x_pixel:.1f}" y2="{top + plot_height}"/>')
        parts.append(f'<text class="label" text-anchor="middle" x="{x_pixel:.1f}" y="{top + plot_height + 25}">{x_value:.1f}</text>')
        y_value = y_min + (y_max - y_min) * tick / 5
        y_pixel = sy(y_value)
        parts.append(f'<line class="grid" x1="{left}" y1="{y_pixel:.1f}" x2="{left + plot_width}" y2="{y_pixel:.1f}"/>')
        parts.append(f'<text class="label" text-anchor="end" x="{left - 10}" y="{y_pixel + 5:.1f}">{y_value:.1f}</text>')
    parts.extend([
        f'<line class="axis" x1="{left}" y1="{top + plot_height}" x2="{left + plot_width}" y2="{top + plot_height}"/>',
        f'<line class="axis" x1="{left}" y1="{top}" x2="{left}" y2="{top + plot_height}"/>',
        f'<text class="label" text-anchor="middle" x="{left + plot_width / 2:.1f}" y="{height - 20}">Elapsed time (s)</text>',
        f'<text class="label" text-anchor="middle" transform="translate(22 {top + plot_height / 2:.1f}) rotate(-90)">{escape(y_label)}</text>',
    ])
    if reference_y is not None:
        reference_pixel = sy(reference_y)
        parts.append(f'<line x1="{left}" y1="{reference_pixel:.1f}" x2="{left + plot_width}" y2="{reference_pixel:.1f}" stroke="#111827" stroke-width="1.5" stroke-dasharray="7 5"/>')
        parts.append(f'<text class="legend" x="{left + plot_width + 20}" y="{reference_pixel + 4:.1f}">{reference_y:g} ms target</text>')
    for series_index, (name, samples, color) in enumerate(series):
        segments: list[list[str]] = [[]]
        for x_value, y_value in zip(x, samples):
            if y_value is None or not math.isfinite(y_value):
                if segments[-1]:
                    segments.append([])
                continue
            command = "M" if not segments[-1] else "L"
            segments[-1].append(f"{command}{sx(x_value):.2f},{sy(y_value):.2f}")
        for segment in segments:
            if segment:
                parts.append(f'<path d="{" ".join(segment)}" fill="none" stroke="{color}" stroke-width="1.6"/>')
        legend_y = top + 20 + series_index * 22
        parts.append(f'<line x1="{left + plot_width + 20}" y1="{legend_y - 4}" x2="{left + plot_width + 45}" y2="{legend_y - 4}" stroke="{color}" stroke-width="3"/>')
        parts.append(f'<text class="legend" x="{left + plot_width + 52}" y="{legend_y}">{escape(name)}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")


def plot_run(run: Path, output_dir: Path, servo_id: int) -> list[Path]:
    frames = _load_frames(run)
    elapsed = _elapsed_seconds(frames)
    currents = _servo_series(frames, "current_raw", 6.5)
    if servo_id not in currents:
        raise ValueError(f"servo ID {servo_id} is absent from the recording")
    angles = _servo_series(frames, "angles_deg")
    output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [
        output_dir / "servo-current-all.svg",
        output_dir / f"servo-{servo_id}-current.svg",
        output_dir / f"servo-{servo_id}-position.svg",
        output_dir / "control-timing.svg",
    ]
    _chart(outputs[0], "All servo current", "Signed current (mA)", elapsed, [
        (f"ID {current_id}", samples, COLORS[index % len(COLORS)])
        for index, (current_id, samples) in enumerate(currents.items())
    ])
    _chart(outputs[1], f"Servo {servo_id} current", "Signed current (mA)", elapsed, [
        (f"ID {servo_id}", currents[servo_id], COLORS[(servo_id - 1) % len(COLORS)])
    ])
    _chart(outputs[2], f"Servo {servo_id} position tracking", "Position (degrees)", elapsed, [
        ("Measured", angles[servo_id], "#2563eb"),
        ("Applied target", _target_series(frames, servo_id), "#dc2626"),
    ])
    intervals = [None] + [(later - earlier) * 1_000.0 for earlier, later in zip(elapsed, elapsed[1:])]
    timing = [
        ("Frame interval", intervals, "#111827"),
        ("Android compute", [float(frame.get("frame_compute_ns")) / 1_000_000.0 if isinstance(frame.get("frame_compute_ns"), (int, float)) else None for frame in frames], "#9333ea"),
        ("Firmware frame", [float(frame.get("input_robot_state", {}).get("frame_us")) / 1_000.0 if isinstance(frame.get("input_robot_state", {}).get("frame_us"), (int, float)) else None for frame in frames], "#16a34a"),
        ("Current read", [float(frame.get("input_robot_state", {}).get("current_us")) / 1_000.0 if isinstance(frame.get("input_robot_state", {}).get("current_us"), (int, float)) else None for frame in frames], "#ea580c"),
        ("Inference", [float(frame.get("inference_ms")) if isinstance(frame.get("inference_ms"), (int, float)) else None for frame in frames], "#0891b2"),
        ("Command to feedback", [float(frame.get("command_to_feedback_ns")) / 1_000_000.0 if isinstance(frame.get("command_to_feedback_ns"), (int, float)) else None for frame in frames], "#be123c"),
    ]
    _chart(outputs[3], "Control timing", "Milliseconds", elapsed, timing, reference_y=20.0)
    return outputs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("--servo-id", type=int, default=1)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    output_dir = args.output_dir or args.run.with_suffix("")
    try:
        outputs = plot_run(args.run, output_dir, args.servo_id)
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
