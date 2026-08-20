from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

import numpy as np
import run_policy as run_policy_module

from run_policy import (
    GravityEstimator,
    NumpyPolicy,
    POLICY_JOINT_SEMANTICS,
    POLICY_SERVO_IDS,
    PolicyRunner,
    PolicySerial,
    RobotCalibration,
    disable_and_verify_servo_torque,
    limit_policy_action,
    policy_torque_limit,
    read_telemetry_sample,
    smooth_policy_command,
    state_vectors,
    telemetry_from_state,
)


def write_complete_calibration(path: Path) -> None:
    knee_parents = {2: 1, 5: 4, 8: 7, 11: 10}
    joints = []
    for index in range(12):
        joint = {
            "policy_index": index,
            "semantic": POLICY_JOINT_SEMANTICS[index],
            "servo_id": POLICY_SERVO_IDS[index],
            "zero_deg": 180.0,
            "servo_degrees_per_policy_radian": 180.0 / np.pi,
            "min_deg": 60.0,
            "max_deg": 300.0,
        }
        if index in knee_parents:
            joint["linkage"] = {
                "type": "four_bar_follow",
                "parent_policy_index": knee_parents[index],
                "parent_ratio": 1.0,
            }
        joints.append(joint)
    path.write_text(
        json.dumps(
            {
                "calibrated": True,
                "imu": {
                    "calibrated": True,
                    "body_axis_from_sensor_axis": np.eye(3).tolist(),
                    "gyro_bias_dps": [0.0, 0.0, 0.0],
                    "gravity_sign": 1,
                },
                "joints": joints,
            }
        ),
        encoding="utf-8",
    )


def write_uncalibrated_template(path: Path) -> None:
    source = Path(__file__).with_name("assembly-1-12dof.calibration.json")
    value = json.loads(source.read_text(encoding="utf-8"))
    value["calibrated"] = False
    value["imu"] = {
        "calibrated": False,
        "body_axis_from_sensor_axis": None,
        "gyro_bias_dps": None,
        "gravity_sign": None,
    }
    for joint in value["joints"]:
        joint["zero_deg"] = None
        joint["servo_degrees_per_policy_radian"] = None
        joint["min_deg"] = None
        joint["max_deg"] = None
    path.write_text(json.dumps(value), encoding="utf-8")


class PolicyRuntimeTests(unittest.TestCase):
    def test_deployment_matches_training_action_and_command_filters(self) -> None:
        requested = np.asarray([-1.0, -0.1, 0.2, 1.0], dtype=np.float32)
        previous = np.asarray([0.0, 0.0, 0.0, 0.0], dtype=np.float32)
        np.testing.assert_allclose(
            limit_policy_action(requested, previous, 0.3),
            [-0.3, -0.1, 0.2, 0.3],
            atol=1.0e-7,
        )
        np.testing.assert_allclose(
            smooth_policy_command(
                np.zeros(3, dtype=np.float32),
                np.asarray([0.18, 0.0, -0.25], dtype=np.float32),
                0.4,
            ),
            [0.009, 0.0, -0.0125],
            atol=1.0e-7,
        )

    def test_gravity_estimator_uses_gyro_during_linear_acceleration(self) -> None:
        estimator = GravityEstimator()
        np.testing.assert_allclose(
            estimator.update(
                np.asarray([0.0, 0.0, 1000.0]),
                np.zeros(3),
                -1.0,
                0.02,
            ),
            [0.0, 0.0, -1.0],
            atol=1.0e-6,
        )
        # At 1.5 g the accelerometer direction is dynamically contaminated, so
        # the estimate advances from the gyro instead of treating it as gravity.
        projected = estimator.update(
            np.asarray([1000.0, 0.0, 1100.0]),
            np.asarray([0.0, 1.0, 0.0]),
            -1.0,
            0.02,
        )
        self.assertGreater(float(projected[0]), 0.015)
        self.assertLess(float(projected[2]), -0.99)

    def test_serial_parser_preserves_fragmented_json(self) -> None:
        class FragmentedSerial:
            def __init__(self) -> None:
                self.chunks = [b'{"type":"policy_', b'state","seq":', b'3}\n']

            @property
            def in_waiting(self) -> int:
                return len(self.chunks[0]) if self.chunks else 0

            def read(self, _size: int) -> bytes:
                return self.chunks.pop(0) if self.chunks else b""

        transport = object.__new__(PolicySerial)
        transport.serial = FragmentedSerial()
        transport._receive_buffer = bytearray()
        message = transport._receive_json(time.monotonic() + 0.1)
        self.assertEqual(message, {"type": "policy_state", "seq": 3})

    def test_serial_command_ack_skips_an_unrelated_ok(self) -> None:
        transport = object.__new__(PolicySerial)
        transport._receive_buffer = bytearray(
            b'{"type":"ok","cmd":"policy_disarm"}\n'
            b'{"type":"ok","cmd":"servo_torque"}\n'
        )
        transport.serial = Mock(in_waiting=0)

        message = transport.receive(
            "ok",
            sequence=None,
            timeout=0.1,
            allow_policy_disarmed=True,
            command="servo_torque",
        )

        self.assertEqual(message["cmd"], "servo_torque")

    def test_compact_policy_feedback_flag_is_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            calibration = RobotCalibration(path)
            state = {
                "armed": True,
                "feedback_complete": True,
                "ids": list(POLICY_SERVO_IDS),
                "angles_deg": [180.0] * 12,
                "gyro_dps": [0.0, 0.0, 0.0],
                "accel_mg": [0.0, 0.0, 1000.0],
            }
            imu_terms, joint_position = state_vectors(state, calibration)
            self.assertEqual(imu_terms.shape, (6,))
            self.assertEqual(joint_position.shape, (12,))
            state["feedback_complete"] = False
            with self.assertRaisesRegex(RuntimeError, "incomplete servo feedback"):
                state_vectors(state, calibration)

    def test_dynamic_acceleration_is_normalized_without_a_magnitude_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            calibration = RobotCalibration(path)
            state = {
                "armed": True,
                "feedback_complete": True,
                "ids": list(POLICY_SERVO_IDS),
                "angles_deg": [180.0] * 12,
                "gyro_dps": [0.0, 0.0, 0.0],
                "accel_mg": [0.0, 0.0, 1422.3],
            }

            imu_terms, _ = state_vectors(state, calibration)
            self.assertTrue(np.all(np.isfinite(imu_terms)))
            self.assertAlmostEqual(float(np.linalg.norm(imu_terms[3:])), 1.0, places=5)

    def test_ui_telemetry_uses_servo_ids_and_calibrated_body_axes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["imu"]["body_axis_from_sensor_axis"] = [
                [0.0, -1.0, 0.0],
                [1.0, 0.0, 0.0],
                [0.0, 0.0, 1.0],
            ]
            value["imu"]["gyro_bias_dps"] = [1.0, 2.0, 3.0]
            path.write_text(json.dumps(value), encoding="utf-8")
            calibration = RobotCalibration(path)
            state = {
                "sample_ms": 1234,
                "ids": list(reversed(POLICY_SERVO_IDS)),
                "angles_deg": [170.0 + index for index in range(12)],
                "gyro_dps": [11.0, 22.0, 33.0],
                "accel_mg": [100.0, 200.0, 900.0],
            }

            target = calibration.policy_positions(
                dict(zip(state["ids"], state["angles_deg"], strict=True))
            )
            target[4] += 0.1
            telemetry = telemetry_from_state(
                state,
                calibration,
                requested_policy_position=target + 0.02,
                applied_policy_position=target,
            )

            self.assertEqual(telemetry["sample_ms"], 1234)
            self.assertEqual(telemetry["servo_angles_deg"]["10"], 172.0)
            self.assertEqual(telemetry["servo_angles_deg"]["7"], 181.0)
            np.testing.assert_allclose(
                telemetry["accel_body_g"], [-0.2, 0.1, 0.9], atol=1.0e-6
            )
            np.testing.assert_allclose(
                telemetry["gyro_body_dps"], [-20.0, 10.0, 30.0], atol=1.0e-6
            )
            self.assertEqual(len(telemetry["measured_policy_position_rad"]), 12)
            self.assertEqual(len(telemetry["requested_policy_position_rad"]), 12)
            self.assertEqual(len(telemetry["applied_policy_position_rad"]), 12)
            self.assertAlmostEqual(
                telemetry["policy_position_error_rad"][4], -0.1, places=5
            )
            self.assertAlmostEqual(
                telemetry["max_policy_position_error_deg"], 5.7296, places=3
            )
            np.testing.assert_allclose(
                telemetry["projected_gravity_body"], [-0.21567, 0.10783, 0.97049], atol=1.0e-5
            )
            state["accel_mg"] = [0.0, 0.0, 0.0]
            zero_accel_telemetry = telemetry_from_state(state, calibration)
            self.assertNotIn("projected_gravity_body", zero_accel_telemetry)
            json.dumps(zero_accel_telemetry, allow_nan=False)

    def test_read_only_telemetry_sends_no_motion_or_torque_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            calibration = RobotCalibration(path)
            transport = Mock()
            transport.receive.side_effect = [
                {
                    "type": "state",
                    "measured": {
                        str(servo_id): 170.0 + servo_id
                        for servo_id in range(1, 13)
                    },
                },
                {
                    "type": "imu",
                    "sample_ms": 4567,
                    "accel": {"x": 0.0, "y": 0.0, "z": 1000.0},
                    "gyro": {"x": 1.0, "y": 2.0, "z": 3.0},
                },
            ]

            sample = read_telemetry_sample(transport, calibration)

            self.assertEqual(sample["servo_angles_deg"]["12"], 182.0)
            self.assertEqual(sample["accel_body_g"], [0.0, 0.0, 1.0])
            self.assertEqual(
                transport.send.call_args_list,
                [call({"cmd": "read", "all": True}), call({"cmd": "imu_status"})],
            )

    def test_policy_rejects_non_finite_observation_before_serialization(self) -> None:
        root = Path(__file__).parent
        policy = NumpyPolicy(root / "policy_weights.npz", root / "policy_metadata.json")
        observation = np.zeros(180, dtype=np.float32)
        observation[17] = np.nan
        with self.assertRaisesRegex(ValueError, "non-finite"):
            policy.action(observation)

    def test_calibration_rejects_non_finite_policy_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            calibration = RobotCalibration(path)
            positions = np.zeros(12, dtype=np.float32)
            positions[4] = np.inf
            with self.assertRaisesRegex(RuntimeError, "non-finite"):
                calibration.servo_targets(positions)

    def test_template_calibration_cannot_arm(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_uncalibrated_template(path)
            with self.assertRaisesRegex(ValueError, "calibration flags"):
                RobotCalibration(path)

    def test_portable_actor_enforces_hash_and_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weights = root / "policy_weights.npz"
            arrays = {
                "obs_mean": np.zeros(180, dtype=np.float32),
                "obs_var": np.ones(180, dtype=np.float32),
                "w0": np.zeros((128, 180), dtype=np.float32),
                "b0": np.zeros(128, dtype=np.float32),
                "w1": np.zeros((128, 128), dtype=np.float32),
                "b1": np.zeros(128, dtype=np.float32),
                "w2": np.zeros((128, 128), dtype=np.float32),
                "b2": np.zeros(128, dtype=np.float32),
                "wout": np.zeros((12, 128), dtype=np.float32),
                "bout": np.linspace(-2.0, 2.0, 12, dtype=np.float32),
            }
            np.savez_compressed(weights, **arrays)
            digest = hashlib.sha256(weights.read_bytes()).hexdigest()
            metadata = root / "policy_metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "weights_sha256": digest,
                        "profile_id": "assembly-1-12dof",
                        "profile_sha256": "b25a4a05fa5a6439b82b824d2c2c826f2a9cc5aacc274d75ee8b4d39978035d3",
                        "observation_size": 180,
                        "action_size": 12,
                        "control_hz": 50,
                        "action_scale_rad": 0.25,
                        "action_delta_limit": 0.3,
                        "command_smoothing_time_s": 0.4,
                        "validated_command_limits": {
                            "forward_m_s": [0.0, 0.18],
                            "lateral_m_s": [0.0, 0.0],
                            "yaw_rate_rad_s": [-0.25, 0.25],
                        },
                        "evaluation": {"passed": True, "stage": "goal"},
                    }
                ),
                encoding="utf-8",
            )
            policy = NumpyPolicy(weights, metadata)
            action = policy.action(np.zeros(180, dtype=np.float32))
            self.assertEqual(action.shape, (12,))
            self.assertAlmostEqual(float(action[0]), -1.0)
            self.assertAlmostEqual(float(action[-1]), 1.0)

            metadata_value = json.loads(metadata.read_text(encoding="utf-8"))
            metadata_value["weights_sha256"] = "0" * 64
            metadata.write_text(json.dumps(metadata_value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "weight hash"):
                NumpyPolicy(weights, metadata)

    def test_historical_rear_knee_profile_is_rejected(self) -> None:
        path = Path(__file__).with_name("policy_metadata.json")
        metadata = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotEqual(
            metadata["profile_sha256"],
            "615092eb851641d55c7a919e6798bc6a8a119669ee201d1fa73afb2dab8d5b98",
        )

    def test_four_bar_knees_follow_hip_and_invert_feedback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            calibration = RobotCalibration(path)

            policy_position = np.zeros(12, dtype=np.float32)
            for hip_index, knee_index in ((1, 2), (4, 5), (7, 8), (10, 11)):
                policy_position[hip_index] = 0.2
                policy_position[knee_index] = -0.5
            targets = calibration.servo_targets(policy_position)

            expected_hip = 180.0 + np.degrees(0.2)
            expected_knee_drive = 180.0 + np.degrees(-0.3)
            for hip_index, knee_index in ((1, 2), (4, 5), (7, 8), (10, 11)):
                self.assertAlmostEqual(float(targets[hip_index]), expected_hip, places=4)
                self.assertAlmostEqual(
                    float(targets[knee_index]), expected_knee_drive, places=4
                )

            feedback = {
                servo_id: float(targets[index])
                for index, servo_id in enumerate(calibration.servo_ids)
            }
            np.testing.assert_allclose(
                calibration.policy_positions(feedback), policy_position, atol=1.0e-6
            )

    def test_policy_femur_coordinate_moves_linked_physical_knee(self) -> None:
        root = Path(__file__).parent
        calibration = RobotCalibration(root / "assembly-1-12dof.calibration.json")
        baseline = calibration.servo_targets(np.zeros(12, dtype=np.float32))
        policy_position = np.zeros(12, dtype=np.float32)
        policy_position[4] = np.radians(10.0)  # simulated front-left femur
        targets = calibration.servo_targets(policy_position)

        self.assertAlmostEqual(float(targets[4] - baseline[4]), 10.0, places=4)
        self.assertAlmostEqual(float(targets[5] - baseline[5]), 10.0, places=4)
        unchanged = [index for index in range(12) if index not in (4, 5)]
        np.testing.assert_allclose(targets[unchanged], baseline[unchanged], atol=1.0e-6)

    def test_four_bar_linkage_is_required_on_each_knee(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["joints"][2].pop("linkage")
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing four-bar linkage"):
                RobotCalibration(path)

    def test_physical_feedback_is_reordered_into_simulation_joint_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            for index, joint in enumerate(value["joints"]):
                joint["servo_degrees_per_policy_radian"] *= -1 if index % 2 else 1
            path.write_text(json.dumps(value), encoding="utf-8")
            calibration = RobotCalibration(path)

            # Distinct simulated values make every leg/role permutation visible.
            policy_position = np.asarray(
                [0.01, 0.11, -0.21, 0.31, 0.41, -0.51,
                 0.61, 0.71, -0.81, 0.91, 1.01, -1.11],
                dtype=np.float32,
            )
            physical_targets = calibration.servo_targets(policy_position)
            feedback_by_physical_id = {
                servo_id: float(physical_targets[policy_index])
                for policy_index, servo_id in enumerate(POLICY_SERVO_IDS)
            }

            np.testing.assert_allclose(
                calibration.policy_positions(feedback_by_physical_id),
                policy_position,
                atol=2.0e-6,
            )
            self.assertEqual(
                tuple(calibration.servo_ids),
                (7, 8, 9, 1, 2, 3, 4, 5, 6, 10, 11, 12),
            )

    def test_wrong_simulation_to_robot_leg_permutation_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            # Swap physical FR and FL groups while leaving all IDs unique.
            for index, servo_id in enumerate((1, 2, 3, 7, 8, 9)):
                value["joints"][index]["servo_id"] = servo_id
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "FR=7-9"):
                RobotCalibration(path)

    def test_wrong_training_semantic_order_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["joints"][0]["semantic"], value["joints"][3]["semantic"] = (
                value["joints"][3]["semantic"],
                value["joints"][0]["semantic"],
            )
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "semantic order"):
                RobotCalibration(path)

    def test_non_finite_calibration_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            value = json.loads(path.read_text(encoding="utf-8"))
            value["imu"]["gyro_bias_dps"][0] = float("nan")
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid IMU"):
                RobotCalibration(path)

    def test_ui_runner_cannot_start_with_template_calibration(self) -> None:
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            calibration = Path(directory) / "calibration.json"
            write_uncalibrated_template(calibration)
            runner = PolicyRunner(
                port="COM_FAKE",
                weights=root / "policy_weights.npz",
                metadata=root / "policy_metadata.json",
                calibration=calibration,
            )
            status = runner.status()
            self.assertFalse(status["calibration_ready"])
            self.assertEqual(status["linkage_knees"], 4)
            with self.assertRaisesRegex(ValueError, "calibration is not ready"):
                runner.start({"confirm_lifted": True})

    def test_ui_remote_control_runs_until_stopped_and_turns_torque_off(self) -> None:
        root = Path(__file__).parent
        runner = PolicyRunner(
            port="COM_FAKE",
            weights=root / "policy_weights.npz",
            metadata=root / "policy_metadata.json",
            calibration=root / "assembly-1-12dof.calibration.json",
        )
        runner._remote_command = (0.0, 0.0, 0.0)
        runner._remote_control_active = True
        with patch.object(run_policy_module, "run_trial", return_value="completed") as trial:
            runner._run(
                calibration=runner._calibration,
                torque_percent=65.0,
            )
        self.assertTrue(trial.call_args.kwargs["torque_off_on_exit"])
        self.assertEqual(trial.call_args.kwargs["torque_percent"], 65.0)
        self.assertIsNone(trial.call_args.kwargs["duration"])
        self.assertTrue(callable(trial.call_args.kwargs["command_provider"]))

    def test_ui_remote_command_can_change_while_policy_is_running(self) -> None:
        root = Path(__file__).parent
        runner = PolicyRunner(
            port="COM_FAKE",
            weights=root / "policy_weights.npz",
            metadata=root / "policy_metadata.json",
            calibration=root / "assembly-1-12dof.calibration.json",
        )
        runner._thread = Mock()
        runner._thread.is_alive.return_value = True
        runner._remote_control_active = True

        status = runner.update_command({"forward": 0.12, "yaw_rate": -0.2})

        self.assertEqual(runner._remote_command, (0.12, 0.0, -0.2))
        self.assertTrue(status["remote_control"]["active"])
        self.assertEqual(status["remote_control"]["forward"], 0.12)
        self.assertEqual(status["remote_control"]["yaw_rate"], -0.2)
        self.assertLess(status["remote_control"]["command_age_s"], 0.1)
        with self.assertRaisesRegex(ValueError, "forward command"):
            runner.update_command({"forward": -0.01, "yaw_rate": 0.0})

        runner._last_remote_command_at = time.monotonic() - 3.0
        with self.assertRaisesRegex(RuntimeError, "heartbeat"):
            runner._remote_command_snapshot()

    def test_stand_to_neutral_leaves_verified_pose_held(self) -> None:
        root = Path(__file__).parent
        runner = PolicyRunner(
            port="COM_FAKE",
            weights=root / "policy_weights.npz",
            metadata=root / "policy_metadata.json",
            calibration=root / "assembly-1-12dof.calibration.json",
        )
        with patch.object(
            run_policy_module, "stand_to_neutral", return_value="standing"
        ) as stand:
            runner._run_stand(
                calibration=runner._calibration,
                torque_percent=70.0,
            )

        self.assertTrue(runner.status()["trial"]["holding_torque"])
        self.assertEqual(runner.status()["trial"]["state"], "standing")
        self.assertEqual(stand.call_args.kwargs["torque_percent"], 70.0)

    def test_ui_defaults_to_full_torque_and_requires_neutral_first(self) -> None:
        root = Path(__file__).parent
        runner = PolicyRunner(
            port="COM_FAKE",
            weights=root / "policy_weights.npz",
            metadata=root / "policy_metadata.json",
            calibration=root / "assembly-1-12dof.calibration.json",
        )

        status = runner.status()
        self.assertEqual(status["defaults"]["torque_percent"], 100.0)
        with self.assertRaisesRegex(ValueError, "stand to neutral"):
            runner.start({"confirm_control": True})

    def test_ui_can_update_verified_imu_mounting_yaw(self) -> None:
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            calibration = Path(directory) / "calibration.json"
            calibration.write_text(
                (root / "assembly-1-12dof.calibration.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            runner = PolicyRunner(
                port="COM_FAKE",
                weights=root / "policy_weights.npz",
                metadata=root / "policy_metadata.json",
                calibration=calibration,
            )
            status = runner.save_imu_mount_yaw({"yaw_deg": 37.5})
            value = json.loads(calibration.read_text(encoding="utf-8"))

            self.assertTrue(value["imu"]["calibrated"])
            radians = np.radians(37.5)
            np.testing.assert_allclose(
                value["imu"]["body_axis_from_sensor_axis"],
                [
                    [np.cos(radians), -np.sin(radians), 0.0],
                    [np.sin(radians), np.cos(radians), 0.0],
                    [0.0, 0.0, 1.0],
                ],
                atol=1.0e-7,
            )
            self.assertAlmostEqual(status["imu_mount_yaw_deg"], 37.5, places=5)

    def test_policy_torque_percent_accepts_full_servo_range(self) -> None:
        self.assertEqual(policy_torque_limit(40.0), 400)
        self.assertEqual(policy_torque_limit(99.0), 990)
        self.assertEqual(policy_torque_limit(100.0), 1000)
        for value in (0.0, 100.1, float("nan"), float("inf")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    policy_torque_limit(value)

    def test_ui_shutdown_verifies_all_physical_torque_registers(self) -> None:
        transport = Mock()
        transport.receive.side_effect = [
            {"type": "ok", "cmd": "servo_torque"},
            *[
                {
                    "type": "servo_bus_probe",
                    "id": servo_id,
                    "bytes": [255, 255, servo_id, 3, 0, 0, 0],
                }
                for servo_id in range(1, 13)
            ],
        ]

        disable_and_verify_servo_torque(transport, tuple(range(1, 13)))

        self.assertEqual(
            transport.send.call_args_list[0],
            call({"cmd": "servo_torque", "all": True, "enabled": False}),
        )
        self.assertEqual(transport.receive.call_count, 13)

    def test_ui_center_workflow_saves_only_an_unverified_draft(self) -> None:
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            calibration = Path(directory) / "calibration.json"
            write_uncalibrated_template(calibration)
            runner = PolicyRunner(
                port="COM_FAKE",
                weights=root / "policy_weights.npz",
                metadata=root / "policy_metadata.json",
                calibration=calibration,
            )
            centers = {str(servo_id): 174.0 + servo_id for servo_id in range(1, 13)}
            status = runner.save_center_draft(
                {
                    "confirm": "SAVE_UNVERIFIED_CENTER_DRAFT",
                    "centers": centers,
                }
            )

            value = json.loads(calibration.read_text(encoding="utf-8"))
            self.assertFalse(value["calibrated"])
            self.assertFalse(value["imu"]["calibrated"])
            self.assertEqual(status["calibration_draft"]["center_count"], 12)
            self.assertFalse(status["calibration_ready"])
            self.assertEqual(
                {item["servo_id"]: item["zero_deg"] for item in value["joints"]},
                {servo_id: 174.0 + servo_id for servo_id in range(1, 13)},
            )
            self.assertIsNone(value["joints"][0]["servo_degrees_per_policy_radian"])
            self.assertEqual(
                value["joints"][2]["linkage"],
                {
                    "type": "four_bar_follow",
                    "parent_policy_index": 1,
                    "parent_ratio": 1.0,
                },
            )

    def test_walk_trims_update_verified_policy_centers_and_relative_limits(self) -> None:
        root = Path(__file__).parent
        with tempfile.TemporaryDirectory() as directory:
            calibration = Path(directory) / "calibration.json"
            original = json.loads(
                (root / "assembly-1-12dof.calibration.json").read_text(
                    encoding="utf-8"
                )
            )
            calibration.write_text(json.dumps(original), encoding="utf-8")
            runner = PolicyRunner(
                port="COM_FAKE",
                weights=root / "policy_weights.npz",
                metadata=root / "policy_metadata.json",
                calibration=calibration,
            )
            trims = {
                servo_id: (-2.5 + 0.5 * (servo_id % 6))
                for servo_id in range(1, 13)
            }
            centers = {
                str(item["servo_id"]): float(item["zero_deg"])
                + trims[int(item["servo_id"])]
                for item in original["joints"]
            }

            status = runner.save_center_draft(
                {
                    "confirm": "APPLY_VERIFIED_WALK_CENTERS",
                    "centers": centers,
                }
            )

            updated = json.loads(calibration.read_text(encoding="utf-8"))
            self.assertTrue(updated["calibrated"])
            self.assertTrue(updated["imu"]["calibrated"])
            self.assertTrue(status["calibration_ready"])
            self.assertEqual(status["trial"]["state"], "centers_applied")
            for before, after in zip(original["joints"], updated["joints"], strict=True):
                servo_id = int(before["servo_id"])
                shift = trims[servo_id]
                self.assertAlmostEqual(after["zero_deg"], before["zero_deg"] + shift)
                self.assertAlmostEqual(after["min_deg"], before["min_deg"] + shift)
                self.assertAlmostEqual(after["max_deg"], before["max_deg"] + shift)
                self.assertEqual(
                    after["servo_degrees_per_policy_radian"],
                    before["servo_degrees_per_policy_radian"],
                )
                self.assertEqual(after.get("linkage"), before.get("linkage"))

    def test_ui_center_workflow_requires_all_twelve_finite_centers(self) -> None:
        root = Path(__file__).parent
        runner = PolicyRunner(
            port="COM_FAKE",
            weights=root / "policy_weights.npz",
            metadata=root / "policy_metadata.json",
            calibration=root / "assembly-1-12dof.calibration.json",
        )
        with self.assertRaisesRegex(ValueError, "ids 1 through 12"):
            runner.save_center_draft(
                {
                    "confirm": "SAVE_UNVERIFIED_CENTER_DRAFT",
                    "centers": {str(servo_id): 180 for servo_id in range(1, 12)},
                }
            )
        with self.assertRaisesRegex(ValueError, "finite values"):
            runner.save_center_draft(
                {
                    "confirm": "SAVE_UNVERIFIED_CENTER_DRAFT",
                    "centers": {
                        str(servo_id): float("nan") if servo_id == 12 else 180
                        for servo_id in range(1, 13)
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
