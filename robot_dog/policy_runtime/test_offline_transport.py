"""Production host/parser integration on a deterministic, motor-disabled fake wire.

No serial ports, sockets, models from the network, firmware or hardware are used.
The bundled legacy assembly-1 actor runs on CPU; this is not Pixel/V20 evidence.
"""
from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import numpy as np
import run_policy as runtime
from test_policy_runtime import write_complete_calibration

ROOT = Path(__file__).resolve().parent


class Clock:
    def __init__(self):
        self.now = 1.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class StopEvent:
    def __init__(self, clock):
        self.clock, self.stopped = clock, False

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.clock.sleep(seconds)
        return self.stopped


class MotorDisabledWire:
    """Byte transport only. No physical API and no possibility of motor output."""
    motor_disabled = True

    def __init__(self, clock):
        self.clock = clock
        self.incoming = bytearray()
        self.commands = []
        self.closed = False
        self.open_lines = None
        self.drop_feedback = False
        self.freeze_sample = False
        self.disconnect_at_frame = None
        self.modify_state = lambda value: value
        self.after_command = lambda value: None
        self.sample_base = 100
        self.feedback_fragments = 17
        self.disconnected = False

    def open(self):
        self.open_lines = (self.dtr, self.rts)

    def close(self):
        self.closed = True

    def reset_input_buffer(self):
        self.incoming.clear()

    @property
    def in_waiting(self):
        if self.disconnected:
            raise OSError("synthetic USB disconnect")
        return min(len(self.incoming), self.feedback_fragments)

    def read(self, size):
        data = bytes(self.incoming[:size])
        del self.incoming[:size]
        if not data:
            self.clock.sleep(.001)
        return data

    def flush(self):
        pass

    def queue(self, payload):
        self.incoming.extend((json.dumps(payload, allow_nan=False) + "\n").encode())

    def state(self, seq, targets=None):
        ids = list(range(12, 0, -1))  # deliberately not training order
        sample = self.sample_base if self.freeze_sample else (self.sample_base + seq * 20) & 0xFFFFFFFF
        value = {"type": "policy_state", "armed": True, "seq": seq,
                 "sample_ms": sample, "ids": ids, "feedback_complete": True,
                 "status_errors": [0] * 12,
                 "angles_deg": [180.0 if targets is None else targets[str(i)] for i in ids],
                 "accel_mg": [0., 0., 1000.], "gyro_dps": [0., 0., 0.]}
        return self.modify_state(value)

    def write(self, data):
        if self.disconnected:
            raise OSError("synthetic USB disconnect")
        payload = json.loads(data)
        self.commands.append(payload)
        command = payload["cmd"]
        if command == "hello":
            self.queue({"type": "hello", "policyArmed": False})
        elif command == "policy_arm":
            self.queue(self.state(0))
        elif command == "policy_frame":
            if payload["seq"] == self.disconnect_at_frame:
                self.disconnected = True
                raise OSError("synthetic USB disconnect")
            if not self.drop_feedback:
                self.queue(self.state(payload["seq"], payload["targets"]))
        elif command == "servo_bus_probe":
            ident = payload["id"]
            self.queue({"type": "servo_bus_probe", "id": ident,
                        "bytes": [255, 255, ident, 3, 0, 0, 0]})
        else:
            self.queue({"type": "ok", "cmd": command})
        self.after_command(payload)
        return len(data)


class OfflineTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = runtime.NumpyPolicy(ROOT / "policy_weights.npz", ROOT / "policy_metadata.json")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        calibration_path = Path(self.temp.name) / "calibration.json"
        write_complete_calibration(calibration_path)
        self.calibration = runtime.RobotCalibration(calibration_path)
        self.clock = Clock()
        self.event = StopEvent(self.clock)
        self.wire = MotorDisabledWire(self.clock)
        self.addCleanup(patch.stopall)
        # Replaces the only hardware boundary. All parser, calibration, actor,
        # history/filter, trial loop, shutdown and torque verification are real.
        self.driver = patch.object(runtime, "serial").start()
        self.driver.Serial.return_value = self.wire
        patch.object(runtime.time, "monotonic", self.clock.monotonic).start()
        patch.object(runtime.time, "sleep", self.clock.sleep).start()

    def trial(self, **overrides):
        args = dict(port="FAKE_NO_HARDWARE", policy=self.policy, calibration=self.calibration,
                    forward=.05, lateral=0., yaw_rate=.1, duration=.1,
                    stop_event=self.event, torque_off_on_exit=True)
        args.update(overrides)
        return runtime.run_trial(**args)

    def assert_shutdown(self):
        self.assertTrue(self.wire.closed)
        self.assertIn({"cmd": "policy_disarm"}, self.wire.commands)
        self.assertIn({"cmd": "servo_torque", "all": True, "enabled": False}, self.wire.commands)
        probes = [row["id"] for row in self.wire.commands if row["cmd"] == "servo_bus_probe"]
        self.assertEqual(probes, list(runtime.POLICY_SERVO_IDS))
        self.assertTrue(self.wire.motor_disabled)

    def test_real_actor_complete_loop_has_bounded_targets_and_verified_fake_shutdown(self):
        reports = []
        observations = []
        original = self.policy.action
        def record(observation):
            observations.append(observation.copy())
            return original(observation)
        with patch.object(self.policy, "action", side_effect=record):
            self.assertEqual(self.trial(report=reports.append), "completed")
        frames = [row for row in self.wire.commands if row["cmd"] == "policy_frame"]
        self.assertGreaterEqual(len(frames), 5)
        self.assertEqual([row["seq"] for row in frames], list(range(1, len(frames) + 1)))
        for row in frames:
            target = np.asarray([row["targets"][str(i)] for i in self.calibration.servo_ids])
            self.assertTrue(np.all(target >= self.calibration.min_deg))
            self.assertTrue(np.all(target <= self.calibration.max_deg))
            self.assertTrue(np.all(np.isfinite(target)))
        observation = observations[0].reshape(4, 45)
        np.testing.assert_array_equal(observation, np.tile(observation[0], (4, 1)))
        np.testing.assert_allclose(observation[0, 6:9], [.05, 0., .1])
        np.testing.assert_array_equal(observation[0, 21:33], np.zeros(12))
        self.assertEqual(self.wire.open_lines, (False, False))
        self.assert_shutdown()

    def test_infinite_actor_output_sends_no_target_and_verifies_fake_shutdown(self):
        invalid = self.policy.arrays["bout"].copy()
        invalid[0] = np.inf
        with patch.dict(self.policy.arrays, {"bout": invalid}):
            with self.assertRaisesRegex(ValueError, "non-finite"):
                self.trial()
        self.assertFalse(any(row["cmd"] == "policy_frame" for row in self.wire.commands))
        self.assert_shutdown()

    def test_cancellation_before_start_never_opens_transport(self):
        self.event.set()
        self.assertEqual(self.trial(), "stopped")
        self.driver.Serial.assert_not_called()
        self.assertEqual(self.wire.commands, [])

    def test_runner_cancel_before_start_releases_preexisting_held_torque(self):
        runner = runtime.PolicyRunner(port="FAKE_NO_HARDWARE",
            weights=ROOT / "policy_weights.npz", metadata=ROOT / "policy_metadata.json",
            calibration=ROOT / "assembly-1-12dof.calibration.json")
        runner.metadata_path = Path(self.temp.name) / "policy_metadata.json"
        runner._stop_event = self.event
        self.event.set()
        runner._run(calibration=self.calibration, torque_percent=25.)
        self.assertNotIn("policy_arm", [row["cmd"] for row in self.wire.commands])
        self.assert_shutdown()
        self.assertEqual(runner.status()["trial"]["state"], "stopped")

    def test_cancellation_during_startup_never_arms(self):
        self.wire.after_command = lambda row: self.event.set() if row["cmd"] == "hello" else None
        self.assertEqual(self.trial(), "stopped")
        self.assertNotIn("policy_arm", [row["cmd"] for row in self.wire.commands])
        self.assert_shutdown()

    def test_cancellation_during_inference_sends_no_more_frames(self):
        original = self.policy.action
        def cancel(observation):
            result = original(observation)
            self.event.set()
            return result
        with patch.object(self.policy, "action", side_effect=cancel):
            self.assertEqual(self.trial(), "stopped")
        self.assertNotIn("policy_frame", [row["cmd"] for row in self.wire.commands])
        self.assert_shutdown()

    def test_feedback_loss_disarms_at_host_deadline(self):
        self.wire.drop_feedback = True
        with self.assertRaisesRegex(RuntimeError, "feedback is stale"):
            self.trial(duration=1.)
        self.assertLess(self.clock.now, 1.2)
        self.assert_shutdown()

    def test_inference_stall_never_sends_a_stale_frame(self):
        original = self.policy.action
        def delayed(observation):
            result = original(observation)
            self.clock.sleep(.2)
            return result
        with patch.object(self.policy, "action", side_effect=delayed):
            with self.assertRaisesRegex(RuntimeError, "stale after inference"):
                self.trial()
        self.assertNotIn("policy_frame", [row["cmd"] for row in self.wire.commands])
        self.assert_shutdown()

    def test_trial_deadline_during_inference_sends_no_late_frame(self):
        original = self.policy.action
        def delayed(observation):
            result = original(observation)
            self.clock.sleep(.11)
            return result
        with patch.object(self.policy, "action", side_effect=delayed):
            self.assertEqual(self.trial(), "completed")
        self.assertNotIn("policy_frame", [row["cmd"] for row in self.wire.commands])
        self.assert_shutdown()

    def test_repeated_sequence_with_advancing_sensor_timestamp_remains_fresh(self):
        self.wire.modify_state = lambda value: {**value, "seq": 0}
        self.assertEqual(self.trial(duration=.2), "completed")
        self.assert_shutdown()

    def test_replayed_sequence_and_sample_timestamp_cannot_extend_deadline(self):
        self.wire.modify_state = lambda value: {**value, "seq": 0, "sample_ms": 100}
        with self.assertRaisesRegex(RuntimeError, "feedback is stale"):
            self.trial(duration=1.)
        self.assert_shutdown()

    def test_current_firmware_waits_for_periodic_tick_before_first_actor_input(self):
        self.wire.modify_state = lambda value: {**value, "tick": value["seq"]}
        def after(row):
            if row["cmd"] == "policy_arm":
                state = self.wire.state(0)
                state.update(tick=1, sample_ms=120, gyro_dps=[180., 0., 0.])
                self.wire.queue(state)
        self.wire.after_command = after
        observations = []
        original = self.policy.action
        def record(observation):
            observations.append(observation.copy())
            return original(observation)
        with patch.object(self.policy, "action", side_effect=record):
            self.assertEqual(self.trial(), "completed")
        self.assertAlmostEqual(float(observations[0][0]), math.pi, places=6)
        self.assert_shutdown()

    def test_current_firmware_tick_zero_alone_cannot_start_actor(self):
        self.wire.modify_state = lambda value: {**value, "tick": 0}
        with patch.object(self.policy, "action", wraps=self.policy.action) as action:
            with self.assertRaises((RuntimeError, TimeoutError)):
                self.trial()
            action.assert_not_called()
        self.assert_shutdown()

    def test_new_sequences_with_frozen_sensor_timestamp_still_expire(self):
        self.wire.freeze_sample = True
        with self.assertRaisesRegex(RuntimeError, "feedback is stale"):
            self.trial(duration=1.)
        self.assert_shutdown()

    def test_uint32_sensor_clock_wrap_is_valid(self):
        self.wire.sample_base = 0xFFFFFFFF - 30
        self.assertEqual(self.trial(), "completed")
        self.assert_shutdown()

    def test_backward_sensor_clock_fails_closed(self):
        self.wire.modify_state = lambda value: {**value, "sample_ms": 99} if value["seq"] else value
        with self.assertRaisesRegex(RuntimeError, "timestamp moved backwards"):
            self.trial()
        self.assert_shutdown()

    def test_ahead_of_sent_feedback_fails_closed(self):
        self.wire.modify_state = lambda value: {**value, "seq": value["seq"] + 1} if value["seq"] else value
        with self.assertRaisesRegex(RuntimeError, "ahead of sent"):
            self.trial()
        self.assert_shutdown()

    def test_disconnect_closes_without_automatic_rearm_and_fresh_session_resets_sequence(self):
        self.wire.disconnect_at_frame = 2
        with self.assertRaisesRegex(RuntimeError, "could not verify physical torque-off"):
            self.trial()
        self.assertTrue(self.wire.closed)
        self.assertEqual(sum(row["cmd"] == "policy_arm" for row in self.wire.commands), 1)
        self.wire = MotorDisabledWire(self.clock)
        self.driver.Serial.return_value = self.wire
        self.assertEqual(self.trial(), "completed")
        self.assertEqual(next(row["seq"] for row in self.wire.commands if row["cmd"] == "policy_frame"), 1)
        self.assert_shutdown()

    def test_invalid_remote_command_disarms_before_a_frame(self):
        with self.assertRaisesRegex(ValueError, "finite"):
            self.trial(command_provider=lambda: (math.nan, 0., 0.))
        self.assertNotIn("policy_frame", [row["cmd"] for row in self.wire.commands])
        self.assert_shutdown()

    def test_invalid_initial_sensor_data_never_reaches_actor(self):
        self.wire.modify_state = lambda value: {**value, "angles_deg": [None] * 12}
        with patch.object(self.policy, "action", wraps=self.policy.action) as action:
            with self.assertRaisesRegex(RuntimeError, "finite numeric angles"):
                self.trial()
            action.assert_not_called()
        self.assert_shutdown()

    def test_handshake_timeout_closes_open_port(self):
        self.wire.queue = lambda value: None
        with self.assertRaisesRegex(TimeoutError, "boot/hello"):
            runtime.PolicySerial("FAKE_NO_HARDWARE")
        self.assertTrue(self.wire.closed)
        self.assertNotIn("policy_arm", [row["cmd"] for row in self.wire.commands])

    def test_open_retries_close_failed_handles_without_motion(self):
        self.driver.Serial.side_effect = [self.wire, MotorDisabledWire(self.clock)]
        self.wire.open = lambda: (_ for _ in ()).throw(OSError("synthetic unavailable port"))
        transport = runtime.PolicySerial("FAKE_NO_HARDWARE")
        self.assertTrue(self.wire.closed)
        self.assertEqual(self.driver.Serial.call_count, 2)
        self.assertEqual(self.wire.commands, [])
        transport.close()

    def stand(self):
        return runtime.stand_to_neutral(
            port="FAKE_NO_HARDWARE", calibration=self.calibration,
            torque_percent=25., stop_event=self.event)

    def prepare_stand(self):
        original = self.wire.write
        def write(data):
            payload = json.loads(data)
            if payload["cmd"] == "read":
                self.wire.commands.append(payload)
                self.wire.queue({"type": "state", "measured": {str(i): 180. for i in range(1, 13)},
                                 "statusErrors": {str(i): 0 for i in range(1, 13)}})
                return len(data)
            return original(data)
        self.wire.write = write

    def test_stand_cancel_before_start_never_opens_transport(self):
        self.event.set()
        self.assertEqual(self.stand(), "stopped")
        self.driver.Serial.assert_not_called()

    def test_stand_cancel_before_start_releases_preexisting_held_torque(self):
        self.event.set()
        result = runtime.stand_to_neutral(port="FAKE_NO_HARDWARE", calibration=self.calibration,
            torque_percent=25., stop_event=self.event, torque_already_enabled=True)
        self.assertEqual(result, "stopped")
        self.assertIn({"cmd": "servo_torque", "all": True, "enabled": False}, self.wire.commands)
        self.assertEqual(sum(row["cmd"] == "servo_bus_probe" for row in self.wire.commands), 12)
        self.assertTrue(self.wire.closed)

    def test_stand_cancel_after_enable_verifies_torque_before_reporting_stopped(self):
        self.prepare_stand()
        self.wire.after_command = lambda row: self.event.set() if row.get("enabled") is True else None
        self.assertEqual(self.stand(), "stopped")
        commands = self.wire.commands
        self.assertNotIn("play", [row["cmd"] for row in commands])
        self.assertIn({"cmd": "servo_torque", "all": True, "enabled": False}, commands)
        self.assertEqual(sum(row["cmd"] == "servo_bus_probe" for row in commands), 12)
        self.assertTrue(self.wire.closed)

    def test_stand_cancel_with_shutdown_disconnect_cannot_claim_stopped(self):
        self.prepare_stand()
        self.wire.after_command = lambda row: self.event.set() if row.get("enabled") is True else None
        with patch.object(runtime, "disable_and_verify_servo_torque", side_effect=OSError("synthetic disconnect")):
            with self.assertRaisesRegex(RuntimeError, "stand shutdown could not verify physical torque-off"):
                self.stand()
        self.assertTrue(self.wire.closed)

    def test_stand_lost_enable_ack_still_attempts_verified_shutdown(self):
        self.prepare_stand()
        original = self.wire.after_command
        def after(row):
            if row.get("enabled") is True:
                self.wire.incoming.clear()
            original(row)
        self.wire.after_command = after
        with self.assertRaises(TimeoutError):
            self.stand()
        self.assertIn({"cmd": "servo_torque", "all": True, "enabled": False}, self.wire.commands)
        self.assertEqual(sum(row["cmd"] == "servo_bus_probe" for row in self.wire.commands), 12)
        self.assertTrue(self.wire.closed)

    def femur(self, **overrides):
        args = dict(port="FAKE_NO_HARDWARE", calibration=self.calibration,
                    policy_leg="FL", delta_deg=10., torque_percent=25., stop_event=self.event)
        args.update(overrides)
        return runtime.run_policy_femur_mix_test(**args)

    def test_femur_cancel_before_cold_start_never_opens_transport(self):
        self.event.set()
        self.assertEqual(self.femur(), {"stopped": True})
        self.driver.Serial.assert_not_called()

    def test_femur_cancel_during_hold_never_sends_return_motion(self):
        self.prepare_stand()
        self.wire.after_command = lambda row: self.event.set() if row["cmd"] == "center_pose" else None
        self.assertEqual(self.femur(), {"stopped": True})
        self.assertEqual(sum(row["cmd"] == "center_pose" for row in self.wire.commands), 1)
        self.assertIn({"cmd": "servo_torque", "all": True, "enabled": False}, self.wire.commands)
        self.assertEqual(sum(row["cmd"] == "servo_bus_probe" for row in self.wire.commands), 12)
        self.assertTrue(self.wire.closed)

    def test_femur_cancel_before_warm_start_verifies_shutdown(self):
        self.event.set()
        self.assertEqual(self.femur(torque_already_enabled=True), {"stopped": True})
        self.assertEqual(sum(row["cmd"] == "servo_bus_probe" for row in self.wire.commands), 12)
        self.assertTrue(self.wire.closed)

    def test_femur_completed_motion_retains_linkage_result_and_disables_torque(self):
        self.prepare_stand()
        result = self.femur()
        self.assertAlmostEqual(result["femur_servo_delta_deg"], 10., places=4)
        self.assertAlmostEqual(result["knee_servo_delta_deg"], 10., places=4)
        self.assertEqual(sum(row["cmd"] == "center_pose" for row in self.wire.commands), 2)
        self.assertEqual(sum(row["cmd"] == "servo_bus_probe" for row in self.wire.commands), 12)
        self.assertTrue(self.wire.closed)


class FramingAndFeedbackTests(unittest.TestCase):
    def raw_transport(self, chunks):
        class Chunks:
            @property
            def in_waiting(self):
                return len(chunks[0]) if chunks else 0
            def read(self, _size):
                return chunks.pop(0) if chunks else b""
        transport = object.__new__(runtime.PolicySerial)
        transport.serial = Chunks()
        transport._receive_buffer = bytearray()
        return transport

    def test_fragmented_invalid_unicode_nonobjects_and_constants_are_ignored(self):
        transport = self.raw_transport([b'boot\r\n\xff\nnull\n[]\n{"x":NaN}\n{"type":', b'"ok"}\n'])
        self.assertEqual(transport.receive_available(), [{"type": "ok"}])

    def test_oversized_fragment_discards_valid_looking_suffix_until_newline(self):
        transport = self.raw_transport([b'x' * (runtime.SERIAL_MAX_LINE_BYTES + 1),
                                       b'{"type":"unsafe_suffix"}\n{"type":"ok"}\n'])
        self.assertEqual(transport.receive_available(), [{"type": "ok"}])

    def test_many_valid_lines_are_not_dropped_as_one_large_frame(self):
        messages = [{"type": "policy_state", "seq": index} for index in range(1000)]
        payload = b''.join((json.dumps(row) + '\n').encode() for row in messages)
        transport = self.raw_transport([payload])
        self.assertEqual(transport.receive_available(), messages)

    def test_receive_preserves_partial_line_across_calls(self):
        transport = self.raw_transport([b'{"type":"ok"'])
        self.assertEqual(transport.receive_available(), [])
        transport.serial = self.raw_transport([b'}\n']).serial
        self.assertEqual(transport.receive_available(), [{"type": "ok"}])

    def test_short_serial_write_is_not_success(self):
        transport = self.raw_transport([])
        transport.serial.write = lambda data: len(data) - 1
        with self.assertRaisesRegex(RuntimeError, "complete command"):
            transport.send({"cmd": "hello"})

    def test_nonfinite_command_is_rejected_before_write(self):
        transport = self.raw_transport([])
        with self.assertRaises(ValueError):
            transport.send({"cmd": "policy_frame", "seq": math.nan})

    def test_sequence_requires_actual_uint32(self):
        for value in [None, True, False, 1.5, "1", -1, 2**32, math.nan]:
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                runtime.protocol_uint32(value, "sequence")
        for value in [0, 1, 2**32 - 1]:
            self.assertEqual(runtime.protocol_uint32(value, "sequence"), value)

    def test_missing_pyserial_only_blocks_hardware_boundary(self):
        with patch.object(runtime, "serial", None):
            policy = runtime.NumpyPolicy(ROOT / "policy_weights.npz", ROOT / "policy_metadata.json")
            self.assertEqual(policy.action(np.zeros(180, dtype=np.float32)).shape, (12,))
            with self.assertRaisesRegex(RuntimeError, "requires pyserial"):
                runtime.PolicySerial("NEVER_OPEN")

    def test_invalid_feedback_shapes_ids_status_and_finiteness_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            calibration = runtime.RobotCalibration(path)
            valid = MotorDisabledWire(Clock()).state(0)
            mutations = [
                {"armed": "true"}, {"feedback_complete": 1},
                {"feedback_complete": None, "feedback": []},
                {"feedback_complete": None, "feedback": [True] * 11},
                {"ids": [1] * 12}, {"ids": [float(i) for i in range(1, 13)]},
                {"angles_deg": [math.nan] * 12}, {"angles_deg": [math.inf] * 12},
                {"angles_deg": ["180"] * 12}, {"angles_deg": [180] * 11},
                {"status_errors": [0] * 11 + [1]}, {"status_errors": []},
                {"gyro_dps": [0, 0]}, {"accel_mg": [[0, 0, 1000]]},
                {"gyro_dps": [0, math.nan, 0]}, {"accel_mg": [0, 0, 0]},
            ]
            for mutation in mutations:
                with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                    runtime.state_vectors({**copy.deepcopy(valid), **mutation}, calibration)
            legacy = {**valid, "feedback_complete": None, "feedback": [True] * 12}
            runtime.state_vectors(legacy, calibration)

    def test_imu_degrees_per_second_are_converted_to_radians(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.json"
            write_complete_calibration(path)
            calibration = runtime.RobotCalibration(path)
            state = MotorDisabledWire(Clock()).state(0)
            state["gyro_dps"] = [180., -90., 0.]
            terms, _ = runtime.state_vectors(state, calibration)
            np.testing.assert_allclose(terms[:3], [math.pi, -math.pi / 2., 0.], rtol=1e-6)
            np.testing.assert_array_equal(terms[3:], [0., 0., 1.])

    def test_gravity_estimator_rejects_invalid_data_without_poisoning_history(self):
        estimator = runtime.GravityEstimator()
        before = estimator.update(np.asarray([0., 0., 1000.]), np.zeros(3), -1., .02)
        for accel, gyro, dt in (([math.nan, 0, 1000], [0, 0, 0], .02),
                                ([1e30, 0, 1000], [0, 0, 0], .02),
                                ([0, 0, 1000], [0, math.inf, 0], .02),
                                ([0, 0, 1000], [0, 0, 0], 0.)):
            with self.subTest(accel=accel, gyro=gyro, dt=dt), np.errstate(over="ignore"):
                with self.assertRaises(RuntimeError):
                    estimator.update(np.asarray(accel), np.asarray(gyro), -1., dt)
                np.testing.assert_array_equal(estimator.projected_gravity, before)



class RunnerCancellationRaceTests(unittest.TestCase):
    def make_runner(self):
        return runtime.PolicyRunner(port="FAKE_NO_HARDWARE",
            weights=ROOT / "policy_weights.npz", metadata=ROOT / "policy_metadata.json",
            calibration=ROOT / "assembly-1-12dof.calibration.json")

    def test_stop_linearizes_with_start_replacing_cancellation_event(self):
        for mode in ("start", "stand", "femur_test", "start_telemetry"):
            with self.subTest(mode=mode):
                runner = self.make_runner()
                runner._holding_torque = mode == "start"
                entered, release, stop_attempted = (threading.Event() for _ in range(3))
                mutex = threading.Lock()
                class TrackedLock:
                    def __enter__(self):
                        if threading.current_thread().name == "stop-race":
                            stop_attempted.set()
                        mutex.acquire()
                    def __exit__(self, *args):
                        mutex.release()
                runner._lock = TrackedLock()
                original_refresh = runner._refresh_calibration
                def refresh():
                    if threading.current_thread().name == "start-race":
                        entered.set()
                        if not release.wait(2.):
                            raise RuntimeError("test barrier timed out")
                    original_refresh()
                results, failures = [], []
                event_name = "_telemetry_stop_event" if mode == "start_telemetry" else "_stop_event"
                worker_name = {"start": "_run", "stand": "_run_stand", "femur_test": "_run_femur_test", "start_telemetry": "_run_telemetry"}[mode]
                def worker(**kwargs):
                    results.append(getattr(runner, event_name).wait(2.))
                def launch():
                    try:
                        if mode == "start_telemetry":
                            runner.start_telemetry()
                        else:
                            getattr(runner, mode)({"confirm_control": True})
                    except Exception as error:
                        failures.append(error)
                with patch.object(runner, "_refresh_calibration", side_effect=refresh), patch.object(runner, worker_name, side_effect=worker):
                    starter = threading.Thread(target=launch, name="start-race")
                    stop_method = runner.stop_telemetry if mode == "start_telemetry" else runner.stop
                    stopper = threading.Thread(target=stop_method, name="stop-race")
                    try:
                        starter.start()
                        self.assertTrue(entered.wait(2.))
                        stopper.start()
                        self.assertTrue(stop_attempted.wait(2.))
                    finally:
                        release.set()
                        starter.join(3.)
                        if stopper.ident is not None:
                            stopper.join(3.)
                        runner.join(3.)
                self.assertEqual(failures, [])
                self.assertEqual(results, [True])
                self.assertTrue(getattr(runner, event_name).is_set())

    def test_cancel_after_stand_returns_disables_held_pose_before_success(self):
        runner = self.make_runner()
        def completed_stand(**kwargs):
            runner._stop_event.set()
            return "standing"
        transport = Mock()
        with patch.object(runtime, "stand_to_neutral", side_effect=completed_stand), \
             patch.object(runtime, "PolicySerial", return_value=transport), \
             patch.object(runtime, "disable_and_verify_servo_torque") as disable:
            runner._run_stand(calibration=runner._calibration, torque_percent=25.)
        disable.assert_called_once_with(transport, runtime.POLICY_SERVO_IDS)
        transport.close.assert_called_once()
        self.assertFalse(runner._holding_torque)
        self.assertEqual(runner.status()["trial"]["state"], "stopped")

    def test_cancel_after_stand_returns_reports_unverified_shutdown(self):
        runner = self.make_runner()
        def completed_stand(**kwargs):
            runner._stop_event.set()
            return "standing"
        with patch.object(runtime, "stand_to_neutral", side_effect=completed_stand), \
             patch.object(runtime, "PolicySerial", return_value=Mock()), \
             patch.object(runtime, "disable_and_verify_servo_torque", side_effect=RuntimeError("unverified torque")):
            runner._run_stand(calibration=runner._calibration, torque_percent=25.)
        self.assertEqual(runner.status()["trial"]["state"], "error")
        self.assertIn("unverified torque", runner.status()["trial"]["message"])

    def test_stop_after_held_pose_published_schedules_shutdown_even_if_worker_is_alive(self):
        runner = self.make_runner()
        runner._holding_torque = True
        runner._thread = Mock()
        runner._thread.is_alive.return_value = True
        with patch.object(runner, "_run_torque_off") as shutdown:
            runner.stop()
            runner.join(2.)
        shutdown.assert_called_once()
        self.assertFalse(runner._holding_torque)

    def test_stand_handoff_preserves_preexisting_torque_ownership(self):
        runner = self.make_runner()
        runner._holding_torque = True
        with patch.object(runtime.threading, "Thread") as thread:
            runner.stand({"torque_percent": 25.})
        self.assertIs(thread.call_args.kwargs["kwargs"]["torque_already_enabled"], True)


if __name__ == "__main__":
    unittest.main()
