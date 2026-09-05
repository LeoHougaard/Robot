"""Compare bounded servo response models on separate windows of a real capture.

These are effective closed-loop response models, not isolated motor parameters.
The firmware programs 1200 encoder steps/s and acceleration register 30. Fit in
servo coordinates using acknowledged targets, not logical knee coordinates.
"""

import argparse
import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np


SPEED_DEG_S = 1200 * 360 / 4096


def load_capture(path):
    with zipfile.ZipFile(path) as archive:
        names = [name for name in archive.namelist() if name.endswith(".jsonl")]
        if len(names) != 1:
            raise ValueError("expected exactly one recorded run")
        records = [json.loads(line) for line in archive.read(names[0]).splitlines() if line.strip()]
    frames = [r["data"] for r in records if r["type"] == "derived_policy_frame"]
    context = next(r["data"]["context"] for r in records if r["type"] == "session_start")
    times = np.array([f["firmware_sample_ms"] for f in frames], dtype=np.int64)
    times = ((times - times[0]) & 0xffffffff) / 1000.0
    measured, targets = [], []
    for frame in frames:
        state = frame["input_robot_state"]
        angles = dict(zip(state["ids"], state["angles_deg"]))
        measured.append([angles[sid] for sid in range(1, 13)])
        targets.append([frame["input_applied_servo_target_deg"][str(sid)] for sid in range(1, 13)])
    return times, np.asarray(targets), np.asarray(measured), context


def predict(times, targets, initial, parameters):
    """Vectorized independent model candidates, with no feedback after reset."""
    times, targets, initial = map(np.asarray, (times, targets, initial))
    if (times.ndim != 1 or len(times) < 2 or targets.shape != (len(times), 12)
            or initial.shape != (12,) or not parameters):
        raise ValueError("expected increasing sample times, 12 complete servo targets, and model candidates")
    if not all(np.isfinite(x).all() for x in (times, targets, initial)) or np.any(np.diff(times) <= 0):
        raise ValueError("sample times, targets, and initial positions must be finite; times must increase")
    for model in parameters:
        name = {"acceleration_limited": "acceleration_deg_s2", "first_order": "tau_s"}.get(model["kind"])
        if name is None or not np.isfinite(model[name]) or model[name] <= 0 or not np.isfinite(model["delay_s"]) or model["delay_s"] < 0:
            raise ValueError("invalid response model parameters")
    position = np.broadcast_to(initial, (len(parameters), 12)).copy()
    velocity = np.zeros_like(position)
    prediction = np.empty((len(times), len(parameters), 12))
    prediction[0] = position
    acceleration = np.array([p.get("acceleration_deg_s2", 1) for p in parameters])[:, None]
    tau = np.array([p.get("tau_s", 1) for p in parameters])[:, None]
    delay = np.array([p["delay_s"] for p in parameters])
    profiled = np.array([p["kind"] == "acceleration_limited" for p in parameters])[:, None]
    for index in range(1, len(times)):
        dt = times[index] - times[index - 1]
        if dt <= 0:
            raise ValueError("firmware sample clock must increase")
        target_indices = np.searchsorted(times, times[index - 1] - delay, side="right") - 1
        requested = targets[np.clip(target_indices, 0, index - 1)]
        # Integrate the internal target at 5 ms or finer, independently of the
        # recorded outer-loop jitter. Do not inject measured state each frame.
        substeps = max(1, int(np.ceil(dt / .005 - 1e-9)))
        h = dt / substeps
        for _ in range(substeps):
            error = requested - position
            braking_speed = np.sqrt(2 * acceleration * np.abs(error))
            desired = np.sign(error) * np.minimum(SPEED_DEG_S, braking_speed)
            velocity += np.clip(desired - velocity, -acceleration * h, acceleration * h)
            profiled_position = position + velocity * h
            first_order_position = position + np.clip(
                (1 - np.exp(-h / tau)) * error, -SPEED_DEG_S * h, SPEED_DEG_S * h
            )
            position = np.where(profiled, profiled_position, first_order_position)
        prediction[index] = position
    return prediction


def fit_capture(path):
    times, targets, measured, context = load_capture(path)
    if len(times) < 1000 or times[-1] < 40:
        raise ValueError("need at least 40 seconds of complete recorded motion")
    parameters = [
        dict(kind="acceleration_limited", acceleration_deg_s2=a, delay_s=d)
        for a in (90., 180., 263.671875, 360., 540., 900., 1800.)
        for d in (0., .02, .04, .08, .12)
    ] + [
        dict(kind="first_order", tau_s=t, delay_s=d)
        for t in (.03, .06, .10, .15, .25, .40, .65, 1.)
        for d in (0., .02, .04, .08, .12)
    ]
    # Reserve the entire last 15 seconds. A five-second gap separates model
    # selection and evaluation. Each window initializes once from its first
    # encoder sample; the first second is warmup and excluded from scoring.
    split = times[-1] - 20
    windows = {"fit": (times >= 2) & (times < split), "held_out": times >= split + 5}
    scores = {}
    for label, mask in windows.items():
        t, u, q = times[mask], targets[mask], measured[mask]
        prediction = predict(t, u, q[0], parameters)
        scores[label] = np.sqrt(np.mean((prediction[t >= t[0] + 1] - q[t >= t[0] + 1, None, :]) ** 2, axis=0))
    chosen = scores["fit"].argmin(axis=0)
    joints = []
    for joint in sorted(context["calibration"]["joints"], key=lambda x: x["policy_index"]):
        sid = joint["servo_id"]
        index = chosen[sid - 1]
        joints.append(dict(
            servo_id=sid, semantic=joint["semantic"], policy_index=joint["policy_index"],
            model=parameters[index], speed_limit_deg_s=SPEED_DEG_S,
            fit_rmse_deg=float(scores["fit"][index, sid - 1]),
            held_out_rmse_deg=float(scores["held_out"][index, sid - 1]),
            family_comparison={
                kind: dict(
                    model=parameters[best],
                    fit_rmse_deg=float(scores["fit"][best, sid - 1]),
                    held_out_rmse_deg=float(scores["held_out"][best, sid - 1]),
                )
                for kind in ("acceleration_limited", "first_order")
                for indices in [[i for i, p in enumerate(parameters) if p["kind"] == kind]]
                for best in [indices[int(scores["fit"][indices, sid - 1].argmin())]]
            },
        ))
    return dict(
        schema_version=1, capture_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        firmware_speed_steps_s=1200, firmware_acceleration_register=30,
        fit_window_s=[2, float(split)], held_out_window_s=[float(split + 5), float(times[-1])],
        calibration=context["calibration"], joints=joints,
        limitations=["One closed-loop gait does not identify unloaded motor dynamics or force.",
                     "Held-out windows are from the same run and hardware conditions.",
                     "Do not combine this response model with another fit of the same lag."],
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = fit_capture(args.capture)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    for joint in report["joints"]:
        print(joint["servo_id"], joint["model"], "fit/held-out RMSE deg:",
              round(joint["fit_rmse_deg"], 2), round(joint["held_out_rmse_deg"], 2))
