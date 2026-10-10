"""Stdlib admission/race tests. Real actor/JVM checks are pixel_runtime_checks."""
from dataclasses import replace
import json
from pathlib import Path
import threading
import unittest

from robot_llm.providers import ProviderResult
from robot_llm.runtime_bridge import BridgeError, ShadowCommandBridge
from robot_llm.session import SessionError
from robot_llm.supervisor import CapabilitySnapshot

ROOT = Path(__file__).resolve().parents[2]


def velocity(**changes):
    value = dict(schema_version=1, kind="velocity_request", frame="body",
                 forward_m_s=.02, lateral_m_s=0, yaw_rate_rad_s=0, duration_ms=100)
    return json.dumps(dict(value, **changes))


class Clock:
    now = 1000
    def __call__(self):
        return self.now


class Pending:
    result = None
    closed = False
    def poll(self):
        return self.result
    def close(self):
        self.closed = True


class RuntimeBridgeTests(unittest.TestCase):
    def setUp(self):
        self.caps = CapabilitySnapshot.from_metadata_json(
            (ROOT / "pixel_robot/app/src/main/assets/policy_metadata.json").read_text())
        self.clock = Clock()
        self.bridges = []

    def tearDown(self):
        for bridge in self.bridges:
            bridge.close()

    def bridge(self, **options):
        kwargs = dict(allow_shadow=True, allow_unqualified_metadata=True, clock_ms=self.clock)
        kwargs.update(options)
        bridge = ShadowCommandBridge(self.caps, **kwargs)
        self.bridges.append(bridge)
        return bridge

    def frame(self, bridge, sequence=1):
        return bridge.begin_frame(sample_sequence=sequence, observed_at_ms=self.clock.now)

    def test_default_requires_explicit_shadow_permission(self):
        with self.assertRaises(SessionError):
            ShadowCommandBridge(self.caps)

    def test_unqualified_current_bundle_requires_separate_opt_in(self):
        bridge = self.bridge(allow_unqualified_metadata=False)
        bridge.propose(velocity())
        with self.assertRaises(BridgeError):
            self.frame(bridge)

    def test_real_session_command_and_one_use_ticket(self):
        bridge = self.bridge()
        request = bridge.propose(velocity())
        ticket = self.frame(bridge)
        self.assertEqual(ticket.request_id, request)
        self.assertEqual(ticket.command, (.02, 0, 0))
        self.assertEqual(ticket.capability_digest, self.caps.digest)
        bridge.finish_frame(ticket)
        with self.assertRaises(BridgeError):
            bridge.finish_frame(ticket)
        self.assertTrue(all(not event['physical_execution'] for event in bridge.take_events()))

    def test_structurally_equal_forged_ticket_not_accepted(self):
        bridge = self.bridge()
        bridge.propose(velocity())
        ticket = self.frame(bridge)
        with self.assertRaises(BridgeError):
            bridge.finish_frame(replace(ticket))
        bridge.finish_frame(ticket)

    def test_no_implicit_queue_or_parallel_frame(self):
        bridge = self.bridge()
        bridge.propose(velocity())
        self.frame(bridge)
        with self.assertRaisesRegex(BridgeError, 'in_flight'):
            self.frame(bridge, 2)

    def test_cancel_fences_running_result_and_allows_fresh_request(self):
        bridge = self.bridge()
        bridge.propose(velocity())
        old = self.frame(bridge)
        bridge.cancel()
        bridge.propose(velocity(forward_m_s=.03))
        self.clock.now += 20
        fresh = self.frame(bridge, 2)
        bridge.fail_frame(old)
        with self.assertRaises(BridgeError):
            bridge.finish_frame(old)
        bridge.finish_frame(fresh)

    def test_stop_does_not_wait_for_compute(self):
        bridge = self.bridge()
        bridge.propose(velocity())
        ticket = self.frame(bridge)
        gate = threading.Event()
        result = []
        def slow_compute():
            gate.wait(2)
            try:
                bridge.finish_frame(ticket)
            except BridgeError:
                result.append('fenced')
        thread = threading.Thread(target=slow_compute)
        thread.start()
        bridge.stop()
        with self.assertRaises(SessionError):
            bridge.propose(velocity())
        gate.set()
        thread.join(2)
        self.assertEqual(result, ['fenced'])

    def test_expiry_at_boundary_has_no_result(self):
        bridge = self.bridge(max_sample_age_ms=200)
        bridge.propose(velocity())
        ticket = self.frame(bridge)
        self.clock.now += 100
        with self.assertRaises(BridgeError):
            bridge.finish_frame(ticket)

    def test_compute_latency_counts_against_sample_freshness(self):
        bridge = self.bridge()
        bridge.propose(velocity())
        ticket = self.frame(bridge)
        self.clock.now += 41
        with self.assertRaises(BridgeError):
            bridge.finish_frame(ticket)
        with self.assertRaises(BridgeError):
            self.frame(bridge, 2)

    def test_future_stale_duplicate_and_out_of_order_samples_cancel(self):
        for sequence, timestamp in ((True, 1000), (1, 1001), (1, 900), (-1, 1000)):
            with self.subTest(sequence=sequence, timestamp=timestamp):
                bridge = self.bridge()
                bridge.propose(velocity())
                with self.assertRaises(BridgeError):
                    bridge.begin_frame(sample_sequence=sequence, observed_at_ms=timestamp)
                with self.assertRaises(BridgeError):
                    self.frame(bridge, 3)
        bridge = self.bridge()
        bridge.propose(velocity())
        bridge.finish_frame(self.frame(bridge))
        self.clock.now += 20
        with self.assertRaises(BridgeError):
            self.frame(bridge)

    def test_replayed_timestamp_with_new_sequence_is_rejected(self):
        bridge = self.bridge()
        bridge.propose(velocity())
        bridge.finish_frame(self.frame(bridge))
        with self.assertRaises(BridgeError):
            self.frame(bridge, 2)

    def test_capability_replacement_fences_old_result(self):
        bridge = self.bridge()
        bridge.propose(velocity())
        ticket = self.frame(bridge)
        bridge.replace_capabilities(replace(self.caps, max_duration_ms=500))
        with self.assertRaises(BridgeError):
            bridge.finish_frame(ticket)

    def test_malformed_operator_command_cancels(self):
        bridge = self.bridge()
        bridge.propose(velocity())
        ticket = self.frame(bridge)
        bridge.handle_line('{"op":"arm"}')
        with self.assertRaises(BridgeError):
            bridge.finish_frame(ticket)

    def test_actor_failure_cancels_only_own_live_frame(self):
        bridge = self.bridge()
        bridge.propose(velocity())
        ticket = self.frame(bridge)
        bridge.fail_frame(ticket)
        with self.assertRaises(BridgeError):
            self.frame(bridge, 2)

    def test_clock_failure_closes_and_fences(self):
        bridge = self.bridge()
        bridge.propose(velocity())
        ticket = self.frame(bridge)
        self.clock.now -= 1
        with self.assertRaises(BridgeError):
            bridge.finish_frame(ticket)

    def test_close_and_model_stop_fence(self):
        for operation in ('close', 'model_stop'):
            bridge = self.bridge()
            bridge.propose(velocity())
            ticket = self.frame(bridge)
            if operation == 'close':
                bridge.close()
            else:
                bridge.propose('{"schema_version":1,"kind":"stop_request"}')
            with self.assertRaises(BridgeError):
                bridge.finish_frame(ticket)

    def test_delayed_provider_reply_cannot_resurrect_cancelled_request(self):
        pending = Pending()
        bridge = self.bridge(mode='native-schema', worker_factory=lambda *args: pending)
        bridge.request('Move forward with explicit values.')
        bridge.cancel()
        pending.result = ProviderResult(velocity(), 0)
        bridge.poll()
        self.assertTrue(pending.closed)
        with self.assertRaises(BridgeError):
            self.frame(bridge)

    def test_provider_reported_latency_never_extends_host_deadline(self):
        pending = Pending()
        bridge = self.bridge(mode='native-schema', worker_factory=lambda *args: pending,
                             request_ttl_ms=100)
        bridge.request('Move forward.')
        pending.result = ProviderResult(velocity(), 0)
        self.clock.now += 1  # 99 ms remain, but requested duration is 100 ms.
        bridge.poll()
        with self.assertRaises(BridgeError):
            self.frame(bridge)

    def test_wrong_frame_nan_and_outside_capability_never_yield_command(self):
        for raw in (velocity(frame='world'), velocity(forward_m_s=99), velocity(forward_m_s=float('nan'))):
            bridge = self.bridge()
            bridge.propose(raw)
            with self.assertRaises(BridgeError):
                self.frame(bridge)

    def test_shared_python_kotlin_intent_vectors(self):
        vectors = json.loads((ROOT / 'robot_llm/data/runtime_intent_vectors.json').read_text())['cases']
        self.assertEqual(len(vectors), 32)
        for vector in vectors:
            with self.subTest(name=vector['name']):
                bridge = self.bridge()
                request = bridge.propose(vector['raw'])
                outcomes = [event for event in bridge.take_events() if event['event'] == 'outcome'
                            and event.get('request_id') == request]
                self.assertEqual(outcomes[-1]['state'], vector['expected'])


if __name__ == '__main__':
    unittest.main()
