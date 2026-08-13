"""Run a promoted locomotion policy over the guarded USB serial transport."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

import numpy as np
import serial


CONTROL_HZ = 50
FRAME_DT = 1.0 / CONTROL_HZ
RECOVERABLE_POLICY_STREAM_ERRORS = frozenset(("invalid json", "serial line too long"))
POLICY_FRAME_SIZE = 45
POLICY_HISTORY = 4
SERIAL_BAUD = 921600
EXPECTED_PROFILE_ID = "assembly-1-12dof"
EXPECTED_PROFILE_SHA256 = (
    "b25a4a05fa5a6439b82b824d2c2c826f2a9cc5aacc274d75ee8b4d39978035d3"
)
UI_BIND_HOST = "127.0.0.1"
UI_DEFAULT_PORT = 18765
DEFAULT_POLICY_TORQUE_PERCENT = 100.0
MIN_POLICY_TORQUE_PERCENT = 1.0
MAX_POLICY_TORQUE_PERCENT = 100.0
REMOTE_COMMAND_TIMEOUT_S = 2.0
FOUR_BAR_KNEE_PARENTS = {2: 1, 5: 4, 8: 7, 11: 10}
POLICY_JOINT_SEMANTICS = (
    "front_right_hip_abduction",
    "front_right_hip_flexion",
    "front_right_knee_flexion",
    "front_left_hip_abduction",
    "front_left_hip_flexion",
    "front_left_knee_flexion",
    "back_right_hip_abduction",
    "back_right_hip_flexion",
    "back_right_knee_flexion",
    "back_left_hip_abduction",
    "back_left_hip_flexion",
    "back_left_knee_flexion",
)
# Training uses FR, FL, BR, BL. The physical robot was numbered LF, BR, RF,
# BL, so policy order must be translated explicitly rather than inferred.
POLICY_SERVO_IDS = (7, 8, 9, 1, 2, 3, 4, 5, 6, 10, 11, 12)
POLICY_LEG_MAP = (
    {"policy_leg": "FR", "servo_ids": (7, 8, 9), "diagonal_pair": "FR+BL"},
    {"policy_leg": "FL", "servo_ids": (1, 2, 3), "diagonal_pair": "FL+BR"},
    {"policy_leg": "BR", "servo_ids": (4, 5, 6), "diagonal_pair": "FL+BR"},
    {"policy_leg": "BL", "servo_ids": (10, 11, 12), "diagonal_pair": "FR+BL"},
)
POLICY_FEMUR_INDEX_BY_LEG = {"FR": 1, "FL": 4, "BR": 7, "BL": 10}


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
        if not np.all(np.isfinite(observation)):
            raise ValueError("policy observation contains a non-finite value")
        value = (observation - self.arrays["obs_mean"]) / np.sqrt(
            self.arrays["obs_var"] + 1.0e-5
        )
        value = np.clip(value, -5.0, 5.0)
        for index in range(3):
            value = self._elu(
                self.arrays[f"w{index}"] @ value + self.arrays[f"b{index}"]
            )
        action = np.clip(
            self.arrays["wout"] @ value + self.arrays["bout"], -1.0, 1.0
        ).astype(np.float32)
        if not np.all(np.isfinite(action)):
            raise ValueError("policy action contains a non-finite value")
        return action


class RobotCalibration:
    def __init__(self, path: Path):
        value = json.loads(path.read_text(encoding="utf-8"))
        if not value.get("calibrated") or not value.get("imu", {}).get("calibrated"):
            raise ValueError("joint and IMU calibration flags must both be true")
        joints = sorted(value["joints"], key=lambda item: item["policy_index"])
        if [item["policy_index"] for item in joints] != list(range(12)):
            raise ValueError("calibration must contain policy indices 0 through 11")
        semantics = tuple(str(item.get("semantic", "")) for item in joints)
        if semantics != POLICY_JOINT_SEMANTICS:
            raise ValueError("calibration semantic order does not match training FR/FL/BR/BL order")
        servo_ids = [int(item["servo_id"]) for item in joints]
        if tuple(servo_ids) != POLICY_SERVO_IDS:
            raise ValueError("calibration servo mapping must be FR=7-9, FL=1-3, BR=4-6, BL=10-12")
        required = ("zero_deg", "servo_degrees_per_policy_radian", "min_deg", "max_deg")
        transmission = np.eye(12, dtype=np.float32)
        for item in joints:
            policy_index = int(item["policy_index"])
            if any(item.get(key) is None for key in required):
                raise ValueError(f"incomplete joint calibration: {item['semantic']}")
            joint_values = [float(item[key]) for key in required]
            if not all(math.isfinite(number) for number in joint_values):
                raise ValueError(f"non-finite joint calibration: {item['semantic']}")
            if abs(float(item["servo_degrees_per_policy_radian"])) < 1.0:
                raise ValueError(f"invalid joint direction/scale: {item['semantic']}")
            if not float(item["min_deg"]) < float(item["zero_deg"]) < float(item["max_deg"]):
                raise ValueError(f"zero is outside safe limits: {item['semantic']}")

            linkage = item.get("linkage")
            expected_parent = FOUR_BAR_KNEE_PARENTS.get(policy_index)
            if expected_parent is None:
                if linkage is not None:
                    raise ValueError(f"unexpected linkage on direct joint: {item['semantic']}")
                continue
            if not isinstance(linkage, dict) or linkage.get("type") != "four_bar_follow":
                raise ValueError(f"missing four-bar linkage: {item['semantic']}")
            parent_index = int(linkage.get("parent_policy_index", -1))
            parent_ratio = float(linkage.get("parent_ratio", 0.0))
            if parent_index != expected_parent or not math.isclose(
                parent_ratio, 1.0, abs_tol=1.0e-6
            ):
                raise ValueError(f"invalid four-bar linkage: {item['semantic']}")
            transmission[policy_index, parent_index] = parent_ratio

        imu = value["imu"]
        matrix = np.asarray(imu["body_axis_from_sensor_axis"], dtype=np.float32)
        bias = np.asarray(imu["gyro_bias_dps"], dtype=np.float32)
        gravity_sign = float(imu["gravity_sign"])
        if (
            matrix.shape != (3, 3)
            or bias.shape != (3,)
            or not np.all(np.isfinite(matrix))
            or not np.all(np.isfinite(bias))
            or gravity_sign not in (-1.0, 1.0)
        ):
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
        self.transmission = transmission
        self.inverse_transmission = np.linalg.inv(transmission).astype(np.float32)
        self.imu_matrix = matrix
        self.gyro_bias_dps = bias
        self.gravity_sign = gravity_sign

    def policy_positions(self, angles_by_id: dict[int, float]) -> np.ndarray:
        servo_angles = np.asarray(
            [angles_by_id[servo_id] for servo_id in self.servo_ids], dtype=np.float32
        )
        servo_position = (servo_angles - self.zero_deg) / self.degrees_per_radian
        return (self.inverse_transmission @ servo_position).astype(np.float32)

    def servo_targets(self, policy_positions: np.ndarray) -> np.ndarray:
        if not np.all(np.isfinite(policy_positions)):
            raise RuntimeError("policy requested a non-finite joint position")
        servo_position = self.transmission @ np.asarray(
            policy_positions, dtype=np.float32
        )
        targets = self.zero_deg + servo_position * self.degrees_per_radian
        if not np.all(np.isfinite(targets)):
            raise RuntimeError("policy produced a non-finite servo target")
        return targets

    def policy_positions_from_targets(self, servo_targets: np.ndarray) -> np.ndarray:
        servo_position = (
            np.asarray(servo_targets, dtype=np.float32) - self.zero_deg
        ) / self.degrees_per_radian
        return (self.inverse_transmission @ servo_position).astype(np.float32)


class PolicySerial:
    def __init__(self, port: str):
        # Configure modem-control lines before opening the CP210x port.  The
        # ESP32 auto-reset circuit treats the default DTR/RTS transition as a
        # reset, which would release torque during the policy handoff.
        self._receive_buffer = bytearray()
        last_error: Exception | None = None
        for attempt in range(5):
            self.serial = serial.Serial(
                port=None,
                baudrate=SERIAL_BAUD,
                timeout=0.001,
                write_timeout=0.05,
            )
            self.serial.dtr = False
            self.serial.rts = False
            self.serial.port = port
            try:
                self.serial.open()
                break
            except (OSError, serial.SerialException) as error:
                last_error = error
                self.serial.close()
                if attempt == 4:
                    raise RuntimeError(
                        f"could not open {port} after the USB connection changed: {error}"
                    ) from error
                time.sleep(0.25)
        else:  # pragma: no cover - loop either opens or raises
            raise RuntimeError(f"could not open {port}: {last_error}")
        self.serial.reset_input_buffer()
        self._wait_until_controller_ready()

    def _wait_until_controller_ready(self) -> None:
        """Survive the ESP32 reset commonly caused by opening its USB UART."""
        deadline = time.monotonic() + 25.0
        next_hello = 0.0
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now >= next_hello:
                self.send({"cmd": "hello"})
                next_hello = now + 0.5
            message = self._receive_json(min(deadline, now + 0.05))
            if message is None:
                continue
            if message.get("type") != "hello":
                continue
            if message.get("policyArmed"):
                self.send({"cmd": "policy_disarm"})
                continue
            return
        raise TimeoutError("timed out waiting for ESP32 boot/hello handshake")

    def send(self, payload: dict) -> None:
        line = json.dumps(payload, separators=(",", ":"), allow_nan=False) + "\n"
        self.serial.write(line.encode("utf-8"))
        self.serial.flush()

    def receive(
        self,
        message_type: str,
        *,
        sequence: int | None,
        timeout: float,
        allow_policy_disarmed: bool = False,
        command: str | None = None,
    ) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            message = self._receive_json(deadline)
            if message is None:
                continue
            if message.get("type") == "error" or (
                message.get("type") == "policy_disarmed"
                and not allow_policy_disarmed
            ):
                raise RuntimeError(message.get("message") or message.get("reason") or message)
            if message.get("type") != message_type:
                continue
            if command is not None and message.get("cmd") != command:
                continue
            if sequence is not None and int(message.get("seq", -1)) != sequence:
                continue
            return message
        raise TimeoutError(f"timed out waiting for {message_type}")

    def receive_available(self) -> list[dict]:
        """Return every complete message already delivered by the USB driver."""
        while self.serial.in_waiting:
            self._receive_buffer.extend(self.serial.read(self.serial.in_waiting))
            if len(self._receive_buffer) > 16384:
                self._receive_buffer.clear()
                return []

        messages = []
        while True:
            newline = self._receive_buffer.find(b"\n")
            if newline < 0:
                return messages
            raw = bytes(self._receive_buffer[:newline])
            del self._receive_buffer[: newline + 1]
            if not raw:
                continue
            try:
                message = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(message, dict):
                messages.append(message)

    def _receive_json(self, deadline: float) -> dict | None:
        """Return one JSON line without dropping fragments between USB reads."""
        while time.monotonic() < deadline:
            newline = self._receive_buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self._receive_buffer[:newline])
                del self._receive_buffer[: newline + 1]
                if not raw:
                    continue
                try:
                    message = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(message, dict):
                    return message
                continue

            waiting = self.serial.in_waiting
            chunk = self.serial.read(waiting if waiting > 0 else 1)
            if chunk:
                self._receive_buffer.extend(chunk)
                if len(self._receive_buffer) > 16384:
                    self._receive_buffer.clear()
        return None

    def close(self) -> None:
        self.serial.close()


def disable_and_verify_servo_torque(
    transport: PolicySerial, servo_ids: tuple[int, ...]
) -> None:
    """Disable every policy servo and confirm the physical RAM registers."""
    transport.send({"cmd": "servo_torque", "all": True, "enabled": False})
    transport.receive(
        "ok",
        sequence=None,
        timeout=2.0,
        allow_policy_disarmed=True,
        command="servo_torque",
    )

    for servo_id in servo_ids:
        transport.send(
            {
                "cmd": "servo_bus_probe",
                "id": servo_id,
                "address": 0x28,
                "length": 1,
            }
        )
        response = transport.receive(
            "servo_bus_probe",
            sequence=None,
            timeout=2.0,
            allow_policy_disarmed=True,
        )
        raw = response.get("bytes")
        if (
            response.get("id") != servo_id
            or not isinstance(raw, list)
            or len(raw) < 7
            or raw[2] != servo_id
            or raw[4] != 0
            or raw[5] != 0
        ):
            raise RuntimeError(f"servo {servo_id} torque-off verification failed")


def state_vectors(
    state: dict,
    calibration: RobotCalibration,
) -> tuple[np.ndarray, np.ndarray]:
    feedback_complete = state.get("feedback_complete")
    if feedback_complete is None:
        feedback_complete = all(state.get("feedback", []))
    if not state.get("armed") or not feedback_complete:
        raise RuntimeError("policy state is disarmed or has incomplete servo feedback")
    ids = [int(value) for value in state["ids"]]
    angles_by_id = dict(zip(ids, (float(value) for value in state["angles_deg"]), strict=True))
    joint_position = calibration.policy_positions(angles_by_id)

    gyro_sensor = np.asarray(state["gyro_dps"], dtype=np.float32)
    if not np.all(np.isfinite(gyro_sensor)):
        raise RuntimeError("gyroscope feedback contains a non-finite value")
    gyro_body = calibration.imu_matrix @ (
        (gyro_sensor - calibration.gyro_bias_dps) * (math.pi / 180.0)
    )
    accel_sensor = np.asarray(state["accel_mg"], dtype=np.float32)
    if not np.all(np.isfinite(accel_sensor)):
        raise RuntimeError("accelerometer feedback contains a non-finite value")
    accel_body = calibration.imu_matrix @ accel_sensor
    magnitude = float(np.linalg.norm(accel_body))
    if magnitude < 50.0:
        raise RuntimeError(f"accelerometer vector is unusable: {magnitude:.1f} mg")
    projected_gravity = calibration.gravity_sign * accel_body / magnitude
    return np.concatenate((gyro_body, projected_gravity)).astype(np.float32), joint_position


def telemetry_from_state(state: dict, calibration: RobotCalibration) -> dict:
    """Return compact physical feedback for the local commissioning UI."""
    ids = [int(value) for value in state["ids"]]
    angles = [float(value) for value in state["angles_deg"]]
    if len(ids) != 12 or len(angles) != 12 or not all(
        math.isfinite(value) for value in angles
    ):
        raise RuntimeError("telemetry requires 12 finite servo angles")

    accel_sensor = np.asarray(state["accel_mg"], dtype=np.float32)
    gyro_sensor = np.asarray(state["gyro_dps"], dtype=np.float32)
    if accel_sensor.shape != (3,) or not np.all(np.isfinite(accel_sensor)):
        raise RuntimeError("telemetry accelerometer feedback is invalid")
    if gyro_sensor.shape != (3,) or not np.all(np.isfinite(gyro_sensor)):
        raise RuntimeError("telemetry gyroscope feedback is invalid")

    accel_body_g = calibration.imu_matrix @ accel_sensor / 1000.0
    gyro_body_dps = calibration.imu_matrix @ (
        gyro_sensor - calibration.gyro_bias_dps
    )
    return {
        "sample_ms": int(state["sample_ms"]),
        "servo_angles_deg": {
            str(servo_id): round(angle, 3)
            for servo_id, angle in zip(ids, angles, strict=True)
        },
        "accel_body_g": [round(float(value), 4) for value in accel_body_g],
        "gyro_body_dps": [round(float(value), 3) for value in gyro_body_dps],
    }


def read_telemetry_sample(
    transport: PolicySerial, calibration: RobotCalibration
) -> dict:
    """Read physical feedback without arming torque or sending a target."""
    transport.receive_available()
    transport.send({"cmd": "read", "all": True})
    servo_state = transport.receive("state", sequence=None, timeout=3.0)
    measured = servo_state.get("measured")
    if not isinstance(measured, dict):
        raise RuntimeError("feedback monitor did not receive servo angles")

    transport.send({"cmd": "imu_status"})
    imu_state = transport.receive("imu", sequence=None, timeout=2.0)
    accel = imu_state.get("accel")
    gyro = imu_state.get("gyro")
    if not isinstance(accel, dict) or not isinstance(gyro, dict):
        raise RuntimeError("feedback monitor did not receive IMU axes")

    ids = list(range(1, 13))
    try:
        state = {
            "sample_ms": int(imu_state["sample_ms"]),
            "ids": ids,
            "angles_deg": [float(measured[str(servo_id)]) for servo_id in ids],
            "accel_mg": [float(accel[axis]) for axis in ("x", "y", "z")],
            "gyro_dps": [float(gyro[axis]) for axis in ("x", "y", "z")],
        }
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("feedback monitor received incomplete values") from error
    return telemetry_from_state(state, calibration)


def validate_trial_parameters(
    policy: NumpyPolicy,
    *,
    forward: float,
    lateral: float,
    yaw_rate: float,
    duration: float,
) -> None:
    values = (forward, lateral, yaw_rate, duration)
    if not all(math.isfinite(value) for value in values):
        raise ValueError("policy control values must be finite numbers")
    if not 0.1 <= duration <= 30.0:
        raise ValueError("duration must be 0.1 to 30 seconds")
    if not policy.forward_limits[0] <= forward <= policy.forward_limits[1]:
        raise ValueError("forward command must be 0.0 to 0.18 m/s")
    if not policy.lateral_limits[0] <= lateral <= policy.lateral_limits[1]:
        raise ValueError("this promoted policy has not been trained for lateral commands")
    if not policy.yaw_limits[0] <= yaw_rate <= policy.yaw_limits[1]:
        raise ValueError("yaw-rate command must be -0.25 to 0.25 rad/s")


def policy_torque_limit(torque_percent: float) -> int:
    if not math.isfinite(torque_percent):
        raise ValueError("torque percent must be a finite number")
    if not MIN_POLICY_TORQUE_PERCENT <= torque_percent <= MAX_POLICY_TORQUE_PERCENT:
        raise ValueError("torque percent must be 1 to 100")
    limit = int(round(torque_percent * 10.0))
    if not 0 < limit <= 1000:
        raise ValueError("torque setting is outside the servo controller range")
    return limit


def stand_to_neutral(
    *,
    port: str,
    calibration: RobotCalibration,
    torque_percent: float,
    stop_event: threading.Event,
    report: Callable[[dict], None] | None = None,
) -> str:
    """Move from the measured resting pose to saved neutral in small steps."""
    report = report or (lambda _status: None)
    torque_limit = policy_torque_limit(torque_percent)
    transport = PolicySerial(port)
    torque_enabled = False
    success = False
    try:
        transport.receive_available()
        transport.send({"cmd": "read", "all": True})
        state = transport.receive("state", sequence=None, timeout=4.0)
        measured_raw = state.get("measured")
        errors = state.get("statusErrors", {})
        if not isinstance(measured_raw, dict):
            raise RuntimeError("stand preparation did not receive servo angles")
        measured = {
            servo_id: float(measured_raw[str(servo_id)])
            for servo_id in calibration.servo_ids
            if str(servo_id) in measured_raw
        }
        if len(measured) != 12:
            raise RuntimeError("stand preparation requires feedback from all 12 servos")
        if any(int(errors.get(str(servo_id), 0)) != 0 for servo_id in measured):
            raise RuntimeError("a servo hardware status error prevents standing")
        if not all(math.isfinite(value) for value in measured.values()):
            raise RuntimeError("stand preparation received a non-finite servo angle")

        target_by_id = {
            servo_id: float(calibration.zero_deg[index])
            for index, servo_id in enumerate(calibration.servo_ids)
        }
        maximum_delta = max(
            abs(target_by_id[servo_id] - measured[servo_id])
            for servo_id in calibration.servo_ids
        )
        step_count = max(1, math.ceil(maximum_delta / 3.0))
        if step_count > 24:
            raise RuntimeError(
                "resting pose is more than 72 degrees from neutral; use manual positioning first"
            )

        transport.send(
            {"cmd": "servo_torque_limit", "all": True, "limit": torque_limit}
        )
        transport.receive(
            "ok",
            sequence=None,
            timeout=2.0,
            command="servo_torque_limit",
        )
        transport.send({"cmd": "servo_torque", "all": True, "enabled": True})
        transport.receive(
            "ok", sequence=None, timeout=2.0, command="servo_torque"
        )
        torque_enabled = True

        steps = []
        for step_index in range(1, step_count + 1):
            fraction = step_index / step_count
            poses = {
                str(servo_id): round(
                    measured[servo_id]
                    + (target_by_id[servo_id] - measured[servo_id]) * fraction,
                    4,
                )
                for servo_id in calibration.servo_ids
            }
            steps.append({"ms": 200, "poses": poses, "speed": 180, "accel": 30})
        transport.send({"cmd": "play", "loop": False, "steps": steps})
        transport.receive("ok", sequence=None, timeout=3.0, command="play")

        started = time.monotonic()
        transition_duration = step_count * 0.2 + 0.5
        while time.monotonic() - started < transition_duration:
            if stop_event.wait(0.05):
                transport.send({"cmd": "stop"})
                disable_and_verify_servo_torque(
                    transport, tuple(int(value) for value in calibration.servo_ids)
                )
                torque_enabled = False
                return "stopped"
            report(
                {
                    "state": "standing_up",
                    "elapsed_s": time.monotonic() - started,
                    "step_count": step_count,
                    "torque_percent": torque_percent,
                    "message": "Moving to the saved neutral pose in small steps.",
                }
            )

        transport.receive_available()
        transport.send({"cmd": "read", "all": True})
        final_state = transport.receive("state", sequence=None, timeout=4.0)
        final_raw = final_state.get("measured")
        final_errors = final_state.get("statusErrors", {})
        if not isinstance(final_raw, dict) or any(
            str(servo_id) not in final_raw for servo_id in calibration.servo_ids
        ):
            raise RuntimeError("neutral verification requires feedback from all 12 servos")
        if any(
            int(final_errors.get(str(servo_id), 0)) != 0
            for servo_id in calibration.servo_ids
        ):
            raise RuntimeError("a servo hardware status error occurred while standing")
        deviations = {
            servo_id: abs(float(final_raw[str(servo_id)]) - target_by_id[servo_id])
            for servo_id in calibration.servo_ids
        }
        worst_id = max(deviations, key=deviations.get)
        if deviations[worst_id] > 3.0:
            raise RuntimeError(
                f"neutral pose was not reached: servo {worst_id} is "
                f"{deviations[worst_id]:.1f} degrees away"
            )
        success = True
        return "standing"
    finally:
        if torque_enabled and not success:
            try:
                disable_and_verify_servo_torque(
                    transport, tuple(int(value) for value in calibration.servo_ids)
                )
            except (OSError, RuntimeError, TimeoutError, serial.SerialException):
                pass
        transport.close()


def run_trial(
    *,
    port: str,
    policy: NumpyPolicy,
    calibration: RobotCalibration,
    forward: float,
    lateral: float,
    yaw_rate: float,
    duration: float | None,
    torque_percent: float | None = None,
    stop_event: threading.Event | None = None,
    report: Callable[[dict], None] | None = None,
    torque_off_on_exit: bool = False,
    command_provider: Callable[[], tuple[float, float, float]] | None = None,
) -> str:
    validate_trial_parameters(
        policy,
        forward=forward,
        lateral=lateral,
        yaw_rate=yaw_rate,
        duration=duration if duration is not None else 1.0,
    )
    stop_event = stop_event or threading.Event()
    report = report or (lambda _status: None)
    transport = PolicySerial(port)
    previous_joint_position = None
    previous_sample_ms = None
    history = None
    command = np.asarray((forward, lateral, yaw_rate), dtype=np.float32)

    try:
        if torque_percent is not None:
            torque_limit = policy_torque_limit(torque_percent)
            transport.send(
                {"cmd": "servo_torque_limit", "all": True, "limit": torque_limit}
            )
            transport.receive(
                "ok",
                sequence=None,
                timeout=2.0,
                command="servo_torque_limit",
            )
        transport.send({"cmd": "policy_arm", "confirm": "CALIBRATED_AND_LIFTED"})
        state = transport.receive("policy_state", sequence=0, timeout=2.0)
        sequence = 0
        state_sequence = 0
        actions_by_sequence = {0: np.zeros(12, dtype=np.float32)}
        started = time.monotonic()
        next_tick = started
        last_report = started
        while (
            duration is None or time.monotonic() - started < duration
        ) and not stop_event.is_set():
            if command_provider is not None:
                forward, lateral, yaw_rate = command_provider()
                validate_trial_parameters(
                    policy,
                    forward=forward,
                    lateral=lateral,
                    yaw_rate=yaw_rate,
                    duration=1.0,
                )
                command = np.asarray((forward, lateral, yaw_rate), dtype=np.float32)
            for message in transport.receive_available():
                if (
                    message.get("type") == "error"
                    and message.get("message") in RECOVERABLE_POLICY_STREAM_ERRORS
                ):
                    # One damaged UART line means one dropped 50 Hz command.
                    # Continue with the next frame; the firmware watchdog owns
                    # sustained transport-loss disarming.
                    continue
                if message.get("type") in ("error", "policy_disarmed"):
                    raise RuntimeError(
                        message.get("message") or message.get("reason") or message
                    )
                if message.get("type") != "policy_state":
                    continue
                received_sequence = int(message.get("seq", -1))
                if received_sequence > sequence:
                    raise RuntimeError("policy feedback sequence is ahead of sent commands")
                if received_sequence > state_sequence:
                    state = message
                    state_sequence = received_sequence

            imu_terms, joint_position = state_vectors(state, calibration)
            sample_ms = int(state["sample_ms"])
            if previous_joint_position is None:
                joint_velocity = np.zeros(12, dtype=np.float32)
            else:
                elapsed_ms = (sample_ms - previous_sample_ms) & 0xFFFFFFFF
                dt = elapsed_ms / 1000.0 if 5 <= elapsed_ms <= 100 else FRAME_DT
                joint_velocity = (joint_position - previous_joint_position) / dt
            frame = np.concatenate(
                (
                    imu_terms,
                    command,
                    joint_position,
                    0.05 * joint_velocity,
                    actions_by_sequence[state_sequence],
                )
            ).astype(np.float32)
            if history is None:
                history = np.tile(frame, (POLICY_HISTORY, 1))
            else:
                history[:-1] = history[1:]
                history[-1] = frame

            requested_action = policy.action(history.reshape(-1))
            requested_position = policy.action_scale * requested_action
            applied_targets = calibration.servo_targets(requested_position)
            applied_action = requested_action

            sequence += 1
            actions_by_sequence[sequence] = applied_action
            for old_sequence in tuple(actions_by_sequence):
                if old_sequence < state_sequence - 1:
                    del actions_by_sequence[old_sequence]
            targets = {
                str(servo_id): round(float(applied_targets[index]), 4)
                for index, servo_id in enumerate(calibration.servo_ids)
            }
            transport.send({"cmd": "policy_frame", "seq": sequence, "targets": targets})
            previous_joint_position = joint_position
            previous_sample_ms = sample_ms

            now = time.monotonic()
            report(
                {
                    "sequence": sequence,
                    "elapsed_s": now - started,
                    "forward": forward,
                    "yaw_rate": yaw_rate,
                    "telemetry": telemetry_from_state(state, calibration),
                }
            )
            if now - last_report >= 1.0:
                print(
                    f"seq={sequence} forward={forward:.2f} "
                    f"yaw_rate={yaw_rate:.2f} loop_ms={(now - next_tick + FRAME_DT) * 1000:.1f}",
                    flush=True,
                )
                last_report = now
            next_tick += FRAME_DT
            delay = next_tick - time.monotonic()
            if delay > 0:
                stop_event.wait(delay)

        result = "stopped" if stop_event.is_set() else "completed"
        print(f"Policy session {result}; disarming and holding the last pose.")
        return result
    finally:
        shutdown_error: Exception | None = None
        try:
            transport.send({"cmd": "policy_disarm"})
            if torque_off_on_exit:
                disable_and_verify_servo_torque(
                    transport, tuple(int(value) for value in calibration.servo_ids)
                )
        except (OSError, RuntimeError, TimeoutError, serial.SerialException) as error:
            shutdown_error = error
        finally:
            transport.close()
        if shutdown_error is not None:
            raise RuntimeError(
                f"policy shutdown could not verify physical torque-off: {shutdown_error}"
            ) from shutdown_error


def run_policy_femur_mix_test(
    *,
    port: str,
    calibration: RobotCalibration,
    policy_leg: str,
    delta_deg: float,
    torque_percent: float,
    hold_s: float = 1.25,
) -> dict:
    """Move one simulated femur coordinate and return through the real linkage map."""
    leg = str(policy_leg).upper()
    if leg not in POLICY_FEMUR_INDEX_BY_LEG:
        raise ValueError("policy leg must be FR, FL, BR, or BL")
    delta_deg = float(delta_deg)
    if not math.isfinite(delta_deg) or not -20.0 <= delta_deg <= 20.0 or abs(delta_deg) < 0.1:
        raise ValueError("femur test delta must be between -20 and 20 degrees")
    torque_limit = policy_torque_limit(torque_percent)
    femur_index = POLICY_FEMUR_INDEX_BY_LEG[leg]
    knee_index = femur_index + 1
    femur_servo_id = int(calibration.servo_ids[femur_index])
    knee_servo_id = int(calibration.servo_ids[knee_index])
    test_servo_ids = (femur_servo_id, knee_servo_id)
    transport = PolicySerial(port)
    try:
        transport.send({"cmd": "read", "all": True})
        state = transport.receive("state", sequence=None, timeout=4.0)
        measured = state.get("measured")
        if not isinstance(measured, dict) or any(
            str(servo_id) not in measured for servo_id in calibration.servo_ids
        ):
            raise RuntimeError("femur test requires feedback from all 12 servos")
        baseline_angles = {
            int(servo_id): float(measured[str(servo_id)])
            for servo_id in calibration.servo_ids
        }
        baseline_policy = calibration.policy_positions(baseline_angles)
        target_policy = baseline_policy.copy()
        target_policy[femur_index] += math.radians(delta_deg)
        baseline_targets = calibration.servo_targets(baseline_policy)
        mixed_targets = calibration.servo_targets(target_policy)

        for servo_id in test_servo_ids:
            transport.send(
                {"cmd": "servo_torque_limit", "id": servo_id, "limit": torque_limit}
            )
            transport.receive(
                "ok", sequence=None, timeout=2.0, command="servo_torque_limit"
            )
            transport.send({"cmd": "servo_torque", "id": servo_id, "enabled": True})
            transport.receive("ok", sequence=None, timeout=2.0, command="servo_torque")

        def send_pair(values: np.ndarray) -> None:
            transport.send(
                {
                    "cmd": "center_pose",
                    "poses": {
                        str(femur_servo_id): round(float(values[femur_index]), 4),
                        str(knee_servo_id): round(float(values[knee_index]), 4),
                    },
                    "speed": 180,
                    "accel": 30,
                }
            )
            transport.receive("ok", sequence=None, timeout=3.0, command="center_pose")

        send_pair(mixed_targets)
        time.sleep(hold_s)
        send_pair(baseline_targets)
        time.sleep(0.35)
        return {
            "policy_leg": leg,
            "policy_femur_delta_deg": delta_deg,
            "femur_servo_id": femur_servo_id,
            "knee_servo_id": knee_servo_id,
            "femur_servo_delta_deg": float(
                mixed_targets[femur_index] - baseline_targets[femur_index]
            ),
            "knee_servo_delta_deg": float(
                mixed_targets[knee_index] - baseline_targets[knee_index]
            ),
        }
    finally:
        try:
            disable_and_verify_servo_torque(
                transport, tuple(int(value) for value in calibration.servo_ids)
            )
        finally:
            transport.close()


class PolicyRunner:
    """Own the local policy remote-control session and its serial transport."""

    def __init__(
        self,
        *,
        port: str,
        weights: Path,
        metadata: Path,
        calibration: Path,
    ):
        self.port = port
        self.weights_path = weights
        self.metadata_path = metadata
        self.calibration_path = calibration
        self.policy = NumpyPolicy(weights, metadata)
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._telemetry_thread: threading.Thread | None = None
        self._telemetry_stop_event = threading.Event()
        self._telemetry_sample: dict | None = None
        self._telemetry_source = ""
        self._telemetry_message = "Feedback monitor is off."
        self._remote_control_active = False
        self._remote_command = (0.0, 0.0, 0.0)
        self._last_remote_command_at = 0.0
        self._holding_torque = False
        self._calibration: RobotCalibration | None = None
        self._calibration_error = "calibration has not been checked"
        self._trial_status: dict = {
            "state": "idle",
            "sequence": 0,
            "elapsed_s": 0.0,
            "message": "Ready to check calibration.",
        }
        self._refresh_calibration()

    def _refresh_calibration(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        try:
            calibration = RobotCalibration(self.calibration_path)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            self._calibration = None
            self._calibration_error = str(error)
        else:
            self._calibration = calibration
            self._calibration_error = ""

    def _calibration_draft_status(self) -> dict:
        try:
            value = json.loads(self.calibration_path.read_text(encoding="utf-8"))
            joints = value.get("joints", [])
            center_count = sum(
                1
                for item in joints
                if item.get("zero_deg") is not None
                and math.isfinite(float(item["zero_deg"]))
            )
            return {
                "center_count": center_count,
                "joint_count": len(joints),
                "joint_flag": value.get("calibrated") is True,
                "imu_flag": value.get("imu", {}).get("calibrated") is True,
            }
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
            return {
                "center_count": 0,
                "joint_count": 0,
                "joint_flag": False,
                "imu_flag": False,
            }

    def status(self) -> dict:
        with self._lock:
            self._refresh_calibration()
            running = self._thread is not None and self._thread.is_alive()
            monitoring = (
                self._telemetry_thread is not None
                and self._telemetry_thread.is_alive()
            )
            trial = dict(self._trial_status)
            trial["running"] = running
            trial["holding_torque"] = self._holding_torque
            return {
                "ready": self._calibration is not None and not running,
                "port": self.port,
                "profile_id": self.policy.metadata["profile_id"],
                "profile_sha256": self.policy.metadata["profile_sha256"],
                "weights_sha256": self.policy.metadata["weights_sha256"],
                "calibration_ready": self._calibration is not None,
                "calibration_error": self._calibration_error,
                "calibration_draft": self._calibration_draft_status(),
                "linkage": "four_bar_follow",
                "linkage_knees": 4,
                "imu_mount_yaw_deg": (
                    math.degrees(
                        math.atan2(
                            float(self._calibration.imu_matrix[1, 0]),
                            float(self._calibration.imu_matrix[0, 0]),
                        )
                    )
                    if self._calibration is not None
                    else None
                ),
                "leg_mapping": [
                    {
                        "policy_leg": item["policy_leg"],
                        "servo_ids": list(item["servo_ids"]),
                        "diagonal_pair": item["diagonal_pair"],
                    }
                    for item in POLICY_LEG_MAP
                ],
                "limits": {
                    "forward": self.policy.forward_limits,
                    "lateral": self.policy.lateral_limits,
                    "yaw_rate": self.policy.yaw_limits,
                    "duration": (0.1, 30.0),
                    "torque_percent": (
                        MIN_POLICY_TORQUE_PERCENT,
                        MAX_POLICY_TORQUE_PERCENT,
                    ),
                },
                "defaults": {"torque_percent": DEFAULT_POLICY_TORQUE_PERCENT},
                "remote_control": {
                    "active": self._remote_control_active and running,
                    "forward": self._remote_command[0],
                    "lateral": self._remote_command[1],
                    "yaw_rate": self._remote_command[2],
                    "command_age_s": (
                        max(0.0, time.monotonic() - self._last_remote_command_at)
                        if self._remote_control_active and running
                        else None
                    ),
                    "timeout_s": REMOTE_COMMAND_TIMEOUT_S,
                },
                "telemetry": {
                    "monitoring": monitoring,
                    "source": self._telemetry_source,
                    "message": self._telemetry_message,
                    "sample": self._telemetry_sample,
                },
                "trial": trial,
            }

    def save_imu_mount_yaw(self, request: dict) -> dict:
        yaw_deg = float(request.get("yaw_deg"))
        if not math.isfinite(yaw_deg) or not -180.0 <= yaw_deg <= 180.0:
            raise ValueError("IMU mounting yaw must be from -180 to 180 degrees")
        radians = math.radians(yaw_deg)
        matrix = [
            [math.cos(radians), -math.sin(radians), 0.0],
            [math.sin(radians), math.cos(radians), 0.0],
            [0.0, 0.0, 1.0],
        ]
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("cannot change IMU mounting during robot movement")
            if self._telemetry_thread is not None and self._telemetry_thread.is_alive():
                raise ValueError("stop the feedback monitor before changing IMU mounting")
            value = json.loads(self.calibration_path.read_text(encoding="utf-8"))
            imu = value.get("imu")
            if not isinstance(imu, dict) or imu.get("calibrated") is not True:
                raise ValueError("verified IMU calibration is required")
            imu["body_axis_from_sensor_axis"] = matrix
            temporary = self.calibration_path.with_name(
                f".{self.calibration_path.name}.tmp"
            )
            temporary.write_text(
                json.dumps(value, indent=2) + "\n", encoding="utf-8"
            )
            temporary.replace(self.calibration_path)
            self._refresh_calibration()
            if self._calibration is None:
                raise ValueError(f"updated IMU calibration is invalid: {self._calibration_error}")
            self._trial_status = {
                "state": "imu_mount_updated",
                "sequence": 0,
                "elapsed_s": 0.0,
                "message": f"IMU body mounting yaw set to {yaw_deg:g} degrees.",
            }
        return self.status()

    def save_center_draft(self, request: dict) -> dict:
        """Apply walk-test centers or save them as an unverified draft."""
        confirmation = request.get("confirm")
        if confirmation not in (
            "APPLY_VERIFIED_WALK_CENTERS",
            "SAVE_UNVERIFIED_CENTER_DRAFT",
        ):
            raise ValueError("walk-center confirmation is required")
        raw_centers = request.get("centers")
        if not isinstance(raw_centers, dict):
            raise ValueError("centers must be an object keyed by servo id")
        try:
            centers = {int(key): float(value) for key, value in raw_centers.items()}
        except (TypeError, ValueError) as error:
            raise ValueError("center ids and angles must be numeric") from error
        if set(centers) != set(range(1, 13)):
            raise ValueError("center draft must contain servo ids 1 through 12")
        if not all(math.isfinite(value) and 0.0 <= value <= 360.0 for value in centers.values()):
            raise ValueError("center angles must be finite values from 0 to 360 degrees")

        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("cannot change center calibration during remote control")
            if self._telemetry_thread is not None and self._telemetry_thread.is_alive():
                raise ValueError("stop the feedback monitor before changing center calibration")
            value = json.loads(self.calibration_path.read_text(encoding="utf-8"))
            if value.get("schema_version") != 1 or value.get("robot") != EXPECTED_PROFILE_ID:
                raise ValueError("center draft calibration profile is incompatible")
            joints = value.get("joints")
            if not isinstance(joints, list) or len(joints) != 12:
                raise ValueError("center draft calibration must contain 12 joints")
            servo_ids = [int(item.get("servo_id", -1)) for item in joints]
            semantics = tuple(str(item.get("semantic", "")) for item in sorted(
                joints, key=lambda item: int(item.get("policy_index", -1))
            ))
            ordered_ids = tuple(int(item.get("servo_id", -1)) for item in sorted(
                joints, key=lambda item: int(item.get("policy_index", -1))
            ))
            if semantics != POLICY_JOINT_SEMANTICS or ordered_ids != POLICY_SERVO_IDS:
                raise ValueError("center draft calibration has an invalid simulation-to-robot mapping")

            apply_verified = confirmation == "APPLY_VERIFIED_WALK_CENTERS"
            if apply_verified and not (
                value.get("calibrated") is True
                and value.get("imu", {}).get("calibrated") is True
            ):
                raise ValueError(
                    "verified walk centers can only update an already verified calibration"
                )

            shifts = {}
            for item in joints:
                servo_id = int(item["servo_id"])
                next_zero = centers[servo_id]
                if apply_verified:
                    previous_zero = float(item["zero_deg"])
                    shift = next_zero - previous_zero
                    next_min = float(item["min_deg"]) + shift
                    next_max = float(item["max_deg"]) + shift
                    if not 0.0 <= next_min < next_zero < next_max <= 360.0:
                        raise ValueError(
                            f"center trim would move servo {servo_id} limits outside 0-360 degrees"
                        )
                    item["min_deg"] = next_min
                    item["max_deg"] = next_max
                    shifts[servo_id] = shift
                item["zero_deg"] = next_zero

            if not apply_verified:
                # Draft values can never arm the runtime or silently preserve
                # a previously verified joint flag.
                value["calibrated"] = False
            temporary = self.calibration_path.with_name(
                f".{self.calibration_path.name}.tmp"
            )
            temporary.write_text(
                json.dumps(value, indent=2) + "\n", encoding="utf-8"
            )
            temporary.replace(self.calibration_path)
            self._refresh_calibration()
            if apply_verified:
                largest_id = max(shifts, key=lambda servo_id: abs(shifts[servo_id]))
                self._trial_status = {
                    "state": "centers_applied",
                    "sequence": 0,
                    "elapsed_s": 0.0,
                    "message": (
                        "Applied all 12 Whole Robot Walk Test centers to the learned policy; "
                        f"largest trim was servo {largest_id} at {shifts[largest_id]:+.1f} degrees."
                    ),
                }
        return self.status()

    def start(self, request: dict) -> dict:
        if not (
            request.get("confirm_control") is True
            or request.get("confirm_lifted") is True
        ):
            raise ValueError("explicit operator control confirmation is required")
        forward = float(request.get("forward", 0.0))
        yaw_rate = float(request.get("yaw_rate", 0.0))
        torque_percent = float(
            request.get("torque_percent", DEFAULT_POLICY_TORQUE_PERCENT)
        )
        policy_torque_limit(torque_percent)
        validate_trial_parameters(
            self.policy,
            forward=forward,
            lateral=0.0,
            yaw_rate=yaw_rate,
            duration=1.0,
        )
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("remote policy control is already running")
            if self._telemetry_thread is not None and self._telemetry_thread.is_alive():
                raise ValueError("stop the feedback monitor before starting the policy")
            self._refresh_calibration()
            if self._calibration is None:
                raise ValueError(f"calibration is not ready: {self._calibration_error}")
            if not self._holding_torque:
                raise ValueError("stand to neutral before starting the learned policy")
            calibration = self._calibration
            self._holding_torque = False
            self._remote_command = (forward, 0.0, yaw_rate)
            self._last_remote_command_at = time.monotonic()
            self._remote_control_active = True
            self._stop_event = threading.Event()
            self._trial_status = {
                "state": "starting",
                "sequence": 0,
                "elapsed_s": 0.0,
                "torque_percent": torque_percent,
                "forward": forward,
                "yaw_rate": yaw_rate,
                "message": f"Starting remote control at {torque_percent:g}% torque.",
            }
            self._thread = threading.Thread(
                target=self._run,
                kwargs={
                    "calibration": calibration,
                    "torque_percent": torque_percent,
                },
                name="policy-remote-control",
                daemon=True,
            )
            self._thread.start()
        return self.status()

    def update_command(self, request: dict) -> dict:
        forward = float(request.get("forward", 0.0))
        yaw_rate = float(request.get("yaw_rate", 0.0))
        validate_trial_parameters(
            self.policy,
            forward=forward,
            lateral=0.0,
            yaw_rate=yaw_rate,
            duration=1.0,
        )
        with self._lock:
            running = self._thread is not None and self._thread.is_alive()
            if not running or not self._remote_control_active:
                raise ValueError("start remote policy control before sending commands")
            self._remote_command = (forward, 0.0, yaw_rate)
            self._last_remote_command_at = time.monotonic()
            self._trial_status.update(
                forward=forward,
                yaw_rate=yaw_rate,
                message=(
                    f"Remote command: {forward:.2f} m/s forward, "
                    f"{yaw_rate:+.2f} rad/s yaw."
                ),
            )
        return self.status()

    def _remote_command_snapshot(self) -> tuple[float, float, float]:
        with self._lock:
            if time.monotonic() - self._last_remote_command_at > REMOTE_COMMAND_TIMEOUT_S:
                raise RuntimeError("remote-control command heartbeat was lost")
            return self._remote_command

    def femur_test(self, request: dict) -> dict:
        leg = str(request.get("policy_leg", "FL")).upper()
        delta_deg = float(request.get("delta_deg", 10.0))
        torque_percent = float(
            request.get("torque_percent", DEFAULT_POLICY_TORQUE_PERCENT)
        )
        policy_torque_limit(torque_percent)
        if leg not in POLICY_FEMUR_INDEX_BY_LEG:
            raise ValueError("policy leg must be FR, FL, BR, or BL")
        if not math.isfinite(delta_deg) or not -20.0 <= delta_deg <= 20.0 or abs(delta_deg) < 0.1:
            raise ValueError("femur test delta must be between -20 and 20 degrees")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("a robot movement is already running")
            if self._telemetry_thread is not None and self._telemetry_thread.is_alive():
                raise ValueError("stop the feedback monitor before moving the robot")
            self._refresh_calibration()
            if self._calibration is None:
                raise ValueError(f"calibration is not ready: {self._calibration_error}")
            calibration = self._calibration
            self._holding_torque = False
            self._trial_status = {
                "state": "starting_femur_test",
                "sequence": 0,
                "elapsed_s": 0.0,
                "torque_percent": torque_percent,
                "message": f"Testing {leg} policy femur mixing by {delta_deg:+g} degrees.",
            }
            self._thread = threading.Thread(
                target=self._run_femur_test,
                kwargs={
                    "calibration": calibration,
                    "policy_leg": leg,
                    "delta_deg": delta_deg,
                    "torque_percent": torque_percent,
                },
                name="policy-femur-mix-test",
                daemon=True,
            )
            self._thread.start()
        return self.status()

    def _run_femur_test(
        self,
        *,
        calibration: RobotCalibration,
        policy_leg: str,
        delta_deg: float,
        torque_percent: float,
    ) -> None:
        try:
            result = run_policy_femur_mix_test(
                port=self.port,
                calibration=calibration,
                policy_leg=policy_leg,
                delta_deg=delta_deg,
                torque_percent=torque_percent,
            )
        except Exception as error:
            with self._lock:
                self._holding_torque = False
                self._trial_status.update(
                    state="error", message=str(error), error=str(error)
                )
        else:
            with self._lock:
                self._holding_torque = False
                self._trial_status.update(
                    state="femur_test_completed",
                    result=result,
                    message=(
                        f"{policy_leg} policy femur {delta_deg:+g} degree test complete: "
                        f"servo {result['femur_servo_id']} moved "
                        f"{result['femur_servo_delta_deg']:+.1f} degrees and linked "
                        f"knee servo {result['knee_servo_id']} moved "
                        f"{result['knee_servo_delta_deg']:+.1f} degrees, then both returned."
                    ),
                )

    def stand(self, request: dict) -> dict:
        torque_percent = float(
            request.get("torque_percent", DEFAULT_POLICY_TORQUE_PERCENT)
        )
        policy_torque_limit(torque_percent)
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("a robot movement is already running")
            if self._telemetry_thread is not None and self._telemetry_thread.is_alive():
                raise ValueError("stop the feedback monitor before moving the robot")
            self._refresh_calibration()
            if self._calibration is None:
                raise ValueError(f"calibration is not ready: {self._calibration_error}")
            calibration = self._calibration
            self._holding_torque = False
            self._stop_event = threading.Event()
            self._trial_status = {
                "state": "starting_stand",
                "sequence": 0,
                "elapsed_s": 0.0,
                "torque_percent": torque_percent,
                "message": (
                    f"Preparing a small-step move to neutral at {torque_percent:g}% torque."
                ),
            }
            self._thread = threading.Thread(
                target=self._run_stand,
                kwargs={
                    "calibration": calibration,
                    "torque_percent": torque_percent,
                },
                name="stand-to-neutral",
                daemon=True,
            )
            self._thread.start()
        return self.status()

    def _report(self, update: dict) -> None:
        with self._lock:
            if isinstance(update.get("telemetry"), dict):
                self._telemetry_sample = update["telemetry"]
                self._telemetry_source = "policy"
                self._telemetry_message = "Live feedback from the learned policy."
            self._trial_status.update(update)
            self._trial_status["state"] = "running"
            self._trial_status["message"] = (
                f"Remote command: {self._remote_command[0]:.2f} m/s forward, "
                f"{self._remote_command[2]:+.2f} rad/s yaw."
            )

    def _report_stand(self, update: dict) -> None:
        with self._lock:
            self._trial_status.update(update)

    def _run(
        self,
        *,
        calibration: RobotCalibration,
        torque_percent: float = DEFAULT_POLICY_TORQUE_PERCENT,
    ) -> None:
        try:
            result = run_trial(
                port=self.port,
                policy=self.policy,
                calibration=calibration,
                forward=self._remote_command[0],
                lateral=0.0,
                yaw_rate=self._remote_command[2],
                duration=None,
                torque_percent=torque_percent,
                stop_event=self._stop_event,
                report=self._report,
                torque_off_on_exit=True,
                command_provider=self._remote_command_snapshot,
            )
        except Exception as error:
            with self._lock:
                self._remote_control_active = False
                self._holding_torque = False
                self._trial_status.update(
                    state="error", message=str(error), error=str(error)
                )
        else:
            with self._lock:
                self._remote_control_active = False
                self._holding_torque = False
                self._trial_status.update(
                    state=result,
                    message=(
                        "Remote control completed and policy transport disarmed."
                        if result == "completed"
                        else "Remote control stopped and policy transport disarmed."
                    ),
                )

    def start_telemetry(self) -> dict:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise ValueError("cannot start the feedback monitor during robot movement")
            if self._holding_torque:
                raise ValueError("stop and disarm the held neutral pose before monitoring")
            if self._telemetry_thread is not None and self._telemetry_thread.is_alive():
                raise ValueError("feedback monitor is already running")
            self._refresh_calibration()
            if self._calibration is None:
                raise ValueError(f"calibration is not ready: {self._calibration_error}")
            calibration = self._calibration
            self._telemetry_stop_event = threading.Event()
            self._telemetry_message = "Starting read-only servo and IMU feedback."
            self._telemetry_thread = threading.Thread(
                target=self._run_telemetry,
                kwargs={"calibration": calibration},
                name="feedback-monitor",
                daemon=True,
            )
            self._telemetry_thread.start()
        return self.status()

    def _run_telemetry(self, *, calibration: RobotCalibration) -> None:
        transport: PolicySerial | None = None
        try:
            transport = PolicySerial(self.port)
            while not self._telemetry_stop_event.is_set():
                sample = read_telemetry_sample(transport, calibration)
                with self._lock:
                    self._telemetry_sample = sample
                    self._telemetry_source = "monitor"
                    self._telemetry_message = "Read-only servo and IMU feedback is live."
                self._telemetry_stop_event.wait(0.1)
        except Exception as error:
            with self._lock:
                self._telemetry_message = f"Feedback monitor stopped: {error}"
        finally:
            if transport is not None:
                transport.close()
            with self._lock:
                if not self._telemetry_message.startswith("Feedback monitor stopped:"):
                    self._telemetry_message = "Feedback monitor is off."

    def stop_telemetry(self) -> dict:
        self._telemetry_stop_event.set()
        with self._lock:
            if self._telemetry_thread is not None and self._telemetry_thread.is_alive():
                self._telemetry_message = "Stopping feedback monitor."
        return self.status()

    def _run_stand(
        self,
        *,
        calibration: RobotCalibration,
        torque_percent: float,
    ) -> None:
        try:
            result = stand_to_neutral(
                port=self.port,
                calibration=calibration,
                torque_percent=torque_percent,
                stop_event=self._stop_event,
                report=self._report_stand,
            )
        except Exception as error:
            with self._lock:
                self._holding_torque = False
                self._trial_status.update(
                    state="error", message=str(error), error=str(error)
                )
        else:
            with self._lock:
                self._holding_torque = result == "standing"
                self._trial_status.update(
                    state=result,
                    message=(
                        f"Standing at neutral with {torque_percent:g}% torque. "
                        "Set the drive command and start remote control."
                        if result == "standing"
                        else "Stand transition stopped and torque disabled."
                    ),
                )

    def _run_torque_off(self) -> None:
        try:
            transport = PolicySerial(self.port)
            try:
                disable_and_verify_servo_torque(transport, POLICY_SERVO_IDS)
            finally:
                transport.close()
        except Exception as error:
            with self._lock:
                self._trial_status.update(
                    state="error", message=str(error), error=str(error)
                )
        else:
            with self._lock:
                self._holding_torque = False
                self._trial_status.update(
                    state="stopped", message="Torque disabled and physically verified."
                )

    def stop(self) -> dict:
        self._stop_event.set()
        self._telemetry_stop_event.set()
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                self._trial_status["state"] = "stopping"
                self._trial_status["message"] = "Stopping and disarming."
            elif self._holding_torque:
                self._trial_status["state"] = "stopping"
                self._trial_status["message"] = "Disabling held neutral torque."
                self._thread = threading.Thread(
                    target=self._run_torque_off,
                    name="policy-torque-off",
                    daemon=True,
                )
                self._thread.start()
        return self.status()

    def join(self, timeout: float) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        telemetry_thread = self._telemetry_thread
        if telemetry_thread is not None:
            telemetry_thread.join(timeout)


class PolicyUiHandler(BaseHTTPRequestHandler):
    server_version = "RobotDogPolicyUI/1.0"

    @property
    def runner(self) -> PolicyRunner:
        return self.server.runner  # type: ignore[attr-defined]

    def _send_json(self, status: int, value: dict) -> None:
        payload = json.dumps(value, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.end_headers()
        self.wfile.write(payload)

    def do_OPTIONS(self) -> None:
        self._send_json(200, {"ok": True})

    def do_GET(self) -> None:
        if self.path != "/api/status":
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        self._send_json(200, {"ok": True, **self.runner.status()})

    def do_POST(self) -> None:
        if self.path not in (
            "/api/start",
            "/api/command",
            "/api/stand",
            "/api/test/femur-mix",
            "/api/stop",
            "/api/calibration/centers",
            "/api/calibration/imu-yaw",
            "/api/telemetry/start",
            "/api/telemetry/stop",
        ):
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 0 or length > 4096:
                raise ValueError("request is too large")
            request = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(request, dict):
                raise ValueError("request body must be a JSON object")
            if self.path == "/api/start":
                status = self.runner.start(request)
            elif self.path == "/api/command":
                status = self.runner.update_command(request)
            elif self.path == "/api/stand":
                status = self.runner.stand(request)
            elif self.path == "/api/test/femur-mix":
                status = self.runner.femur_test(request)
            elif self.path == "/api/calibration/centers":
                status = self.runner.save_center_draft(request)
            elif self.path == "/api/calibration/imu-yaw":
                status = self.runner.save_imu_mount_yaw(request)
            elif self.path == "/api/telemetry/start":
                status = self.runner.start_telemetry()
            elif self.path == "/api/telemetry/stop":
                status = self.runner.stop_telemetry()
            else:
                status = self.runner.stop()
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
            self._send_json(400, {"ok": False, "error": str(error)})
            return
        self._send_json(200, {"ok": True, **status})

    def log_message(self, _format: str, *_args: object) -> None:
        return


def serve_policy_ui(runner: PolicyRunner, port: int) -> None:
    server = ThreadingHTTPServer((UI_BIND_HOST, port), PolicyUiHandler)
    server.runner = runner  # type: ignore[attr-defined]
    print(
        f"Policy UI bridge ready at http://{UI_BIND_HOST}:{port}; "
        "leave this window open and use the Learned Policy panel.",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("Stopping policy UI bridge and disarming remote control.")
    finally:
        runner.stop()
        runner.join(3.0)
        server.server_close()


def main() -> None:
    runtime_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True, help="ESP32 serial port, for example COM5")
    parser.add_argument("--weights", type=Path, default=runtime_dir / "policy_weights.npz")
    parser.add_argument("--metadata", type=Path, default=runtime_dir / "policy_metadata.json")
    parser.add_argument(
        "--calibration",
        type=Path,
        default=runtime_dir / "assembly-1-12dof.calibration.json",
    )
    parser.add_argument("--forward", type=float, default=0.0)
    parser.add_argument("--lateral", type=float, default=0.0)
    parser.add_argument("--yaw-rate", type=float, default=0.0)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--confirm-lifted", action="store_true")
    parser.add_argument(
        "--torque-off-on-exit",
        action="store_true",
        help="disable all servo torque before closing serial after a commissioning trial",
    )
    parser.add_argument("--ui", action="store_true", help="serve the local browser UI bridge")
    parser.add_argument("--ui-port", type=int, default=UI_DEFAULT_PORT)
    args = parser.parse_args()

    if args.ui:
        if not 1024 <= args.ui_port <= 65535:
            raise SystemExit("ui-port must be 1024 to 65535")
        runner = PolicyRunner(
            port=args.port,
            weights=args.weights,
            metadata=args.metadata,
            calibration=args.calibration,
        )
        serve_policy_ui(runner, args.ui_port)
        return

    if not args.confirm_lifted:
        raise SystemExit("Refusing to arm: pass --confirm-lifted only after supporting the robot.")
    policy = NumpyPolicy(args.weights, args.metadata)
    try:
        validate_trial_parameters(
            policy,
            forward=args.forward,
            lateral=args.lateral,
            yaw_rate=args.yaw_rate,
            duration=args.duration,
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    calibration = RobotCalibration(args.calibration)
    run_trial(
        port=args.port,
        policy=policy,
        calibration=calibration,
        forward=args.forward,
        lateral=args.lateral,
        yaw_rate=args.yaw_rate,
        duration=args.duration,
        torque_off_on_exit=args.torque_off_on_exit,
    )


if __name__ == "__main__":
    main()
