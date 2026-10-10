"""Required optional-dependency integration gate: real ONNX and real Kotlin math.

Run explicitly with the pinned runtime environment and preinstalled JVM jars.
Missing dependencies are errors, never skipped or replaced with mock inference.
"""
import json
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

from robot_llm.pixel_runtime import (BUNDLE_FILES, CALIBRATION, DEFAULT_ASSETS, PixelActor,
                                     PixelMathProcess, PixelShadowRuntime)
from robot_llm.runtime_bridge import BridgeError, ShadowCommandBridge
from robot_llm.runtime_demo import ReplayClock, math_command, run_demo, synthetic_sensor


def proposal(**changes):
    return json.dumps(dict(dict(schema_version=1, kind='velocity_request', frame='body',
                               forward_m_s=.02, lateral_m_s=0, yaw_rate_rad_s=0, duration_ms=500), **changes))


class PixelRuntimeChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.actor = PixelActor()
        cls.jvm_command = math_command()

    def runtime(self):
        clock = ReplayClock()
        bridge = ShadowCommandBridge(self.actor.capabilities, allow_shadow=True,
                                     allow_unqualified_metadata=True, clock_ms=clock)
        peer = PixelMathProcess(self.jvm_command, timeout_s=5)
        runtime = PixelShadowRuntime(bridge, self.actor, peer)
        self.addCleanup(runtime.close)
        return runtime, bridge, clock

    def step(self, runtime, clock, **changes):
        options = dict(sample_sequence=clock.now // 20, observed_at_ms=clock.now,
                       sensor=synthetic_sensor(), acknowledged_action=[0.0]*12,
                       evidence_kind='synthetic_feedback')
        options.update(changes)
        return runtime.step(**options)

    def changed_bundle(self, name, change):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        target = Path(directory.name)
        for asset in BUNDLE_FILES:
            shutil.copyfile(DEFAULT_ASSETS / asset, target / asset)
        path = target / name
        if name.endswith('.json'):
            value = json.loads(path.read_text())
            change(value)
            path.write_text(json.dumps(value))
        else:
            path.write_bytes(path.read_bytes() + b'changed')
        return target

    def test_exact_actor_cpu_reference_parity(self):
        self.assertEqual(self.actor.parity['cases'], 8)
        self.assertLess(self.actor.parity['max_abs_error'], 2e-6)
        self.assertEqual(self.actor._actor.get_providers(), ['CPUExecutionProvider'])

    def test_wrong_actor_bytes_rejected(self):
        with self.assertRaises(BridgeError):
            PixelActor(self.changed_bundle('policy_actor.onnx', None))

    def test_wrong_manifest_source_weights_rejected(self):
        with self.assertRaises(BridgeError):
            PixelActor(self.changed_bundle('policy_android_manifest.json', lambda d: d.update(source_weights_sha256='0'*64)))

    def test_legacy_observation_metadata_rejected(self):
        with self.assertRaises(BridgeError):
            PixelActor(self.changed_bundle('policy_metadata.json', lambda d: d.update(observation_size=180)))

    def test_reference_hash_mismatch_rejected(self):
        with self.assertRaises(BridgeError):
            PixelActor(self.changed_bundle('stride_reference.json', lambda d: d.update(lift_m=999)))

    def test_calibration_coordinate_mismatch_rejected(self):
        with self.assertRaises(BridgeError):
            PixelActor(self.changed_bundle(CALIBRATION, lambda d: d.update(joint_coordinate_convention='legacy_relative_knee')))

    def test_bad_expected_actor_parity_rejected(self):
        def change(d):
            d['cases'][0]['action'][0] += .1
        with self.assertRaises(BridgeError):
            PixelActor(self.changed_bundle('policy_reference_vectors.json', change))

    def test_nonfinite_wrong_shape_and_bool_actor_input_rejected(self):
        for vector in ([0.0]*180, [float('nan')]*428, [True]*428, ['0']*428):
            with self.subTest(value=vector[0]):
                with self.assertRaises(BridgeError):
                    self.actor.action(vector)

    def test_real_session_kotlin_observation_onnx_and_calibration_join(self):
        runtime, bridge, clock = self.runtime()
        bridge.propose(proposal())
        result = self.step(runtime, clock)
        self.assertEqual(len(result['observation']), 428)
        self.assertEqual(len(result['actor_residual']), 12)
        for start in [6, 76, 146, 216, 286, 356, 420]:
            self.assertAlmostEqual(result['observation'][start], .02, places=7)
            self.assertEqual(result['observation'][start+1:start+3], [0.0, 0.0])
        self.assertEqual(result['observation'][423:426], [0.0]*3)
        self.assertEqual(result['applied_action'], [0.0]*12)
        self.assertEqual(result['servo_ids'], self.actor.servo_ids)
        self.assertFalse(result['physical_execution'])

    def test_real_200_frame_demo_preserves_hold_then_stride_and_expires(self):
        result = run_demo(allow_shadow=True, allow_unqualified_metadata=True)
        self.assertTrue(result['first_50_applied_actions_zero'])
        self.assertTrue(result['nonzero_actions_after_hold'])
        self.assertEqual(result['outcome_states'], ['shadow_accepted', 'expired'])
        self.assertFalse(result['hardware_access'])
        self.assertFalse(result['language_model_inference'])
        self.assertTrue(result['policy_actor_inference'])
        self.assertTrue(result['revoked_frame_publication_rejected'])
        self.assertEqual(result['stop_outcome'], 'stop_requested')

    def test_demo_refuses_implicit_permission(self):
        with self.assertRaises(BridgeError):
            run_demo()

    def test_real_actor_result_fenced_on_cancel(self):
        runtime, bridge, clock = self.runtime()
        bridge.propose(proposal())
        original = self.actor.action
        def cancel_after_actor(observation):
            result = original(observation)
            bridge.cancel()
            return result
        with patch.object(self.actor, 'action', side_effect=cancel_after_actor):
            with self.assertRaisesRegex(BridgeError, '^frame_cancelled_or_stale$'):
                self.step(runtime, clock)

    def test_real_actor_result_fenced_on_trusted_stop(self):
        runtime, bridge, clock = self.runtime()
        bridge.propose(proposal())
        original = self.actor.action
        def stop_after_actor(observation):
            result = original(observation)
            bridge.stop()
            return result
        with patch.object(self.actor, 'action', side_effect=stop_after_actor):
            with self.assertRaisesRegex(BridgeError, '^frame_cancelled_or_stale$'):
                self.step(runtime, clock)

    def test_real_actor_latency_exceeding_deadline_fences_result(self):
        runtime, bridge, clock = self.runtime()
        bridge.propose(proposal(duration_ms=20))
        original = self.actor.action
        def finish_late(observation):
            result = original(observation)
            clock.now = 20
            return result
        with patch.object(self.actor, 'action', side_effect=finish_late):
            with self.assertRaisesRegex(BridgeError, '^frame_cancelled_or_stale$'):
                self.step(runtime, clock)

    def test_actual_jvm_rejects_invalid_sensor_and_no_later_diagnostic(self):
        runtime, bridge, clock = self.runtime()
        bridge.propose(proposal())
        sensor = synthetic_sensor()
        sensor['dt'] = 0
        with self.assertRaises(BridgeError):
            self.step(runtime, clock, sensor=sensor)
        clock.now = 20
        with self.assertRaises(BridgeError):
            self.step(runtime, clock)

    def test_reject_coerced_current_and_unlabeled_samples(self):
        runtime, bridge, clock = self.runtime()
        bridge.propose(proposal())
        with self.assertRaises(BridgeError):
            self.step(runtime, clock, evidence_kind='hardware_verified')
        sensor = synthetic_sensor()
        sensor['current'][0] = '5'
        with self.assertRaises(BridgeError):
            self.step(runtime, clock, sensor=sensor)

    def test_same_actor_new_request_resets_real_kotlin_history(self):
        runtime, bridge, clock = self.runtime()
        bridge.propose(proposal())
        old = self.step(runtime, clock)
        clock.now = 20
        bridge.propose(proposal(forward_m_s=.03))
        fresh = self.step(runtime, clock)
        self.assertNotEqual(old['request_id'], fresh['request_id'])
        self.assertEqual(fresh['applied_action'], [0.0]*12)
        self.assertAlmostEqual(fresh['observation'][6], .03, places=7)

    def test_wrong_jvm_asset_binding_rejected(self):
        runtime, bridge, clock = self.runtime()
        bridge.propose(proposal())
        exchange = runtime.peer.exchange
        def corrupted(value):
            result = exchange(value)
            if value['op'] == 'reset':
                result['metadata_sha256'] = '0' * 64
            return result
        with patch.object(runtime.peer, 'exchange', side_effect=corrupted):
            with self.assertRaisesRegex(BridgeError, 'asset_mismatch'):
                self.step(runtime, clock)

    def test_wrong_jvm_servo_order_rejected(self):
        runtime, bridge, clock = self.runtime()
        bridge.propose(proposal())
        exchange = runtime.peer.exchange
        def corrupted(value):
            result = exchange(value)
            if value['op'] == 'action':
                result['servo_ids'] = list(reversed(result['servo_ids']))
            return result
        with patch.object(runtime.peer, 'exchange', side_effect=corrupted):
            with self.assertRaisesRegex(BridgeError, 'output_identity_mismatch'):
                self.step(runtime, clock)

    def test_actor_exception_is_sanitized_and_cancels_lease(self):
        runtime, bridge, clock = self.runtime()
        bridge.propose(proposal())
        with patch.object(self.actor, 'action', side_effect=RuntimeError('secret diagnostic')):
            with self.assertRaisesRegex(BridgeError, '^runtime_computation_failed$'):
                self.step(runtime, clock)
        clock.now = 20
        with self.assertRaises(BridgeError):
            self.step(runtime, clock)


if __name__ == '__main__':
    unittest.main()
