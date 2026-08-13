"""Run a promoted locomotion policy over the guarded USB serial transport."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
import serial


CONTROL_HZ = 50
FRAME_DT = 1.0 / CONTROL_HZ
POLICY_FRAME_SIZE = 45
POLICY_HISTORY = 4
SERIAL_BAUD = 460800
EXPECTED_PROFILE_ID = "assembly-1-12dof"
EXPECTED_PROFILE_SHA256 = (
    "b25a4a05fa5a6439b82b824d2c2c826f2a9cc5aacc274d75ee8b4d39978035d3"
)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class NumpyPolicy:
    def __init__(self, weights: Path, metadata: Path):
        self.arrays = dict(np.load(weights, allow_pickle=False))
        self.metadata = json.loads(metadata.read_text(encoding="utf-8"))
        if file_sha256(weights) != self.metadata["weights_sha256"]:
            raise ValueError("policy weight hash does not match metadata")
        if self.metadata.get("profile_id") != EXPECTED_PROFILE_ID:
            raise ValueError("policy profile id is not assembly-1-12dof")
        if self.metadata.get("profile_sha256", "").lower() != EXPECTED_PROFILE_SHA256:
            raise ValueError("policy was not exported for the current rear-knee profile")
        if self.metadata["observation_size"] != POLICY_FRAME_SIZE * POLICY_HISTORY:
            raise ValueError("policy observation contract is not 4 x 45")
        if self.metadata["action_size"] != 12 or self.metadata["control_hz"] != CONTROL_HZ:
            raise ValueError("policy action or control-rate contract is incompatible")
        if not self.metadata.get("evaluation", {}).get("passed"):
            raise ValueError("policy metadata does not contain a passing evaluation")
        if self.metadata["evaluation"].get("stage") != "goal":
            raise ValueError("real-robot policy must pass the Goal evaluation")
        limits = self.metadata.get("validated_command_limits", {})
        self.forward_limits = tuple(float(value) for value in limits["forward_m_s"])
        self.lateral_limits = tuple(float(value) for value in limits["lateral_m_s"])
        self.yaw_limits = tuple(float(value) for value in limits["yaw_rate_rad_s"])
        if not (
            self.forward_limits == (0.0, 0.18)
            and self.lateral_limits == (0.0, 0.0)
            and self.yaw_limits == (-0.25, 0.25)
        ):
            raise ValueError("policy command envelope is not the validated commissioning envelope")
        self.action_scale = float(self.metadata["action_scale_rad"])
        if self.action_scale != 0.25:
            raise ValueError("policy action scale is incompatible")

    @staticmethod
    def _elu(value: np.ndarray) -> np.ndarray:
        return np.where(value > 0.0, value, np.expm1(value))

    def action(self, observation: np.ndarray) -> np.ndarray:
        if observation.shape != (POLICY_FRAME_SIZE * POLICY_HISTORY,):
            raise ValueError(f"expected 180 observations, received {observation.shape}")
        value = (observation - self.arrays["obs_mean"]) / np.sqrt(
            self.arrays["obs_var"] + 1.0e-5
        )
        value = np.clip(value, -5.0, 5.0)
        for index in range(3):
            value = self._elu(
                self.arrays[f"w{index}"] @ value + self.arrays[f"b{index}"]
            )
        return np.clip(
            self.arrays["wout"] @ value + self.arrays["bout"], -1.0, 1.0
        ).astype(np.float32)


class RobotCalibration:
    def __init__(self, path: Path):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not value.get("calibrated") or not value.get("imu", {}).get("calibrated"):
            raise ValueError("joint and IMU calibration flags must both be true")
        joints = sorted(value["joints"], key=lambda item: item["policy_index"])
        if [item["policy_index"] for item in joints] != list(range(12)):
            raise ValueError("calibration must contain policy indices 0 through 11")
        servo_ids = [int(item["servo_id"]) for item in joints]
        if set(servo_ids) != set(range(1, 13)):
            raise ValueError("calibration must map unique servo ids 1 through 12")
        required = ("zero_deg", "servo_degrees_per_policy_radian", "min_deg", "max_deg")
        for item in joints:
            if any(item.get(key) is None for key in required):
                raise ValueError(f"incomplete joint calibration: {item['semantic']}")
            if abs(float(item["servo_degrees_per_policy_radian"])) < 1.0:
                raise ValueError(f"invalid joint direction/scale: {item['semantic']}")
            if not float(item["min_deg"]) < float(item["zero_deg"]) < float(item["max_deg"]):
                raise ValueError(f"zero is outside safe limits: {item['semantic']}")

        imu = value["imu"]
        matrix = np.asarray(imu["body_axis_from_sensor_axis"], dtype=np.float32)
        bias = np.asarray(imu["gyro_bias_dps"], dtype=np.float32)
        gravity_sign = float(imu["gravity_sign"])
        if matrix.shape != (3, 3) or bias.shape != (3,) or gravity_sign not in (-1.0, 1.0):
            raise ValueError("invalid IMU axis, bias, or gravity-sign calibration")
        if not np.allclose(matrix @ matrix.T, np.eye(3), atol=1.0e-3):
            raise ValueError("IMU body-axis matrix must be orthonormal")

        self.joints = joints
        self.servo_ids = servo_ids
        self.zero_deg = np.asarray([item["zero_deg"] for item in joints], dtype=np.float32)
        self.degrees_per_radian = np.asarray(
            [item["servo_degrees_per_policy_radian"] for item in joints], dtype=np.float32
        )
        self.min_deg = np.asarray([item["min_deg"] for item in joints], dtype=np.float32)
        self.max_deg = np.asarray([item["max_deg"] for item in joints], dtype=np.float32)
        self.imu_matrix = matrix
        self.gyro_bias_dps = bias
        self.gravity_sign = gravity_sign

    def policy_positions(self, angles_by_id: dict[int, float]) -> np.ndarray:
        servo_angles = np.asarray(
            [angles_by_id[servo_id] for servo_id in self.servo_ids], dtype=np.float32
        )
        return (servo_angles - self.zero_deg) / self.degrees_per_radian

    def servo_targets(self, policy_positions: np.ndarray) -> np.ndarray:
        targets = self.zero_deg + policy_positions * self.degrees_per_radian
        if np.any(targets < self.min_deg) or np.any(targets > self.max_deg):
            raise RuntimeError("policy requested a target outside calibrated limits")
        return targets


class PolicySerial:
    def __init__(self, port: str):
        self.serial = serial.Serial(port, SERIAL_BAUD, timeout=0.02, write_timeout=0.05)
        self.serial.reset_input_buffer()

    def send(self, payload: dict) -> None:
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        self.serial.write(line.encode("utf-8"))
        self.serial.flush()

    def receive(self, message_type: str, *, sequence: int | None, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self.serial.readline()
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if message.get("type") in ("error", "policy_disarmed"):
                raise RuntimeError(message.get("message") or message.get("reason") or message)
            if message.get("type") != message_type:
                continue
            if sequence is not None and int(message.get("seq", -1)) != sequence:
                continue
            return message
        raise TimeoutError(f"timed out waiting for {message_type}")

    def close(self) -> None:
        self.serial.close()


def state_vectors(state: dict, calibration: RobotCalibration) -> tuple[np.ndarray, np.ndarray]:
    if not state.get("armed") or not all(state.get("feedback", [])):
        raise RuntimeError("policy state is disarmed or has incomplete servo feedback")
    ids = [int(value) for value in state["ids"]]
    angles_by_id = dict(zip(ids, (float(value) for value in state["angles_deg"]), strict=True))
    joint_position = calibration.policy_positions(angles_by_id)

    gyro_sensor = np.asarray(state["gyro_dps"], dtype=np.float32)
    gyro_body = calibration.imu_matrix @ (
        (gyro_sensor - calibration.gyro_bias_dps) * (math.pi / 180.0)
    )
    accel_sensor = np.asarray(state["accel_mg"], dtype=np.float32)
    accel_body = calibration.imu_matrix @ accel_sensor
    magnitude = float(np.linalg.norm(accel_body))
    if not 700.0 <= magnitude <= 1300.0:
        raise RuntimeError(f"accelerometer magnitude is unsafe: {magnitude:.1f} mg")
    projected_gravity = calibration.gravity_sign * accel_body / magnitude
    return np.concatenate((gyro_body, projected_gravity)).astype(np.float32), joint_position


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="ESP32 serial port, for example COM5")
    parser.add_argument("--weights", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--forward", type=float, default=0.0)
    parser.add_argument("--lateral", type=float, default=0.0)
    parser.add_argument("--yaw-rate", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--confirm-lifted", action="store_true")
    args = parser.parse_args()

    if not args.confirm_lifted:
        raise SystemExit("Refusing to arm: pass --confirm-lifted only after supporting the robot.")
    if not 0.1 <= args.duration <= 30.0:
        raise SystemExit("duration must be 0.1 to 30 seconds")

    policy = NumpyPolicy(args.weights, args.metadata)
    if not policy.forward_limits[0] <= args.forward <= policy.forward_limits[1]:
        raise SystemExit("forward command must be 0.0 to 0.18 m/s")
    if not policy.lateral_limits[0] <= args.lateral <= policy.lateral_limits[1]:
        raise SystemExit("this promoted policy has not been trained for lateral commands")
    if not policy.yaw_limits[0] <= args.yaw_rate <= policy.yaw_limits[1]:
        raise SystemExit("yaw-rate command must be -0.25 to 0.25 rad/s")
    calibration = RobotCalibration(args.calibration)
    transport = PolicySerial(args.port)
    previous_joint_position = None
    previous_sample_ms = None
    previous_action = np.zeros(12, dtype=np.float32)
    history = None
    command = np.asarray((args.forward, args.lateral, args.yaw_rate), dtype=np.float32)

    try:
        transport.send({"cmd": "policy_arm", "confirm": "CALIBRATED_AND_LIFTED"})
        state = transport.receive("policy_state", sequence=0, timeout=2.0)
        sequence = 0
        started = time.monotonic()
        next_tick = started
        last_report = started
        while time.monotonic() - started < args.duration:
            imu_terms, joint_position = state_vectors(state, calibration)
            sample_ms = int(state["sample_ms"])
            if previous_joint_position is None:
                joint_velocity = np.zeros(12, dtype=np.float32)
            else:
                elapsed_ms = (sample_ms - previous_sample_ms) & 0xFFFFFFFF
                dt = elapsed_ms / 1000.0 if 5 <= elapsed_ms <= 100 else FRAME_DT
                joint_velocity = (joint_position - previous_joint_position) / dt
            frame = np.concatenate(
                (imu_terms, command, joint_position, 0.05 * joint_velocity, previous_action)
            ).astype(np.float32)
            if history is None:
                history = np.tile(frame, (POLICY_HISTORY, 1))
            else:
                history[:-1] = history[1:]
                history[-1] = frame

            requested_action = policy.action(history.reshape(-1))
            requested_position = policy.action_scale * requested_action
            requested_targets = calibration.servo_targets(requested_position)
            current_targets = calibration.servo_targets(joint_position)
            applied_targets = np.clip(
                requested_targets, current_targets - 5.0, current_targets + 5.0
            )
            applied_position = (
                applied_targets - calibration.zero_deg
            ) / calibration.degrees_per_radian
            previous_action = np.clip(
                applied_position / policy.action_scale, -1.0, 1.0
            ).astype(np.float32)

            sequence += 1
            targets = {
                str(servo_id): round(float(applied_targets[index]), 4)
                for index, servo_id in enumerate(calibration.servo_ids)
            }
            transport.send({"cmd": "policy_frame", "seq": sequence, "targets": targets})
            state = transport.receive("policy_state", sequence=sequence, timeout=0.10)
            previous_joint_position = joint_position
            previous_sample_ms = sample_ms

            now = time.monotonic()
            if now - last_report >= 1.0:
                print(
                    f"seq={sequence} forward={args.forward:.2f} "
                    f"yaw_rate={args.yaw_rate:.2f} loop_ms={(now - next_tick + FRAME_DT) * 1000:.1f}",
                    flush=True,
                )
                last_report = now
            next_tick += FRAME_DT
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
        print("Policy trial completed; disarming and holding the last pose.")
    finally:
        try:
            transport.send({"cmd": "policy_disarm"})
        except (OSError, serial.SerialException):
            pass
        transport.close()


if __name__ == "__main__":
    main()
