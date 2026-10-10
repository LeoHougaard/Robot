"""Network-free session tests; child-process tests use only local test doubles."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import patch

from robot_llm.providers import EndpointConfig, InferenceProvider, ProviderResult
from robot_llm.session import (LiveInference, NonblockingJsonlIO, ProcessCall, SessionError, ShadowSession,
                               main, run_jsonl)
from robot_llm.supervisor import CapabilitySnapshot, OutcomeState

ROOT = Path(__file__).resolve().parents[2]
CAP_PATH = ROOT / "pixel_robot/app/src/main/assets/policy_metadata.json"
STOP = '{"schema_version":1,"kind":"stop_request"}'
CLARIFY = '{"schema_version":1,"kind":"clarify","question":"Which speed?"}'


def velocity(**changes):
    return json.dumps(dict({"schema_version": 1, "kind": "velocity_request", "frame": "body",
                            "forward_m_s": .02, "lateral_m_s": 0, "yaw_rate_rad_s": 0,
                            "duration_ms": 100}, **changes))


# Module-level targets can be spawned on Windows as well as POSIX.
def child_success():
    return ProviderResult(CLARIFY, 0, synthetic=True)


def child_hangs():
    time.sleep(60)
    return ProviderResult(STOP, 0, synthetic=True)


def child_raises():
    raise RuntimeError("secret endpoint credential must not leave child")


def child_native_output():
    os.write(1, b"SECRET_NATIVE_STDOUT")
    os.write(2, b"SECRET_NATIVE_STDERR")
    print("SECRET_PYTHON_STDOUT")
    print("SECRET_PYTHON_STDERR", file=sys.stderr)
    return ProviderResult(CLARIFY, 0, synthetic=True)


class FakeClock:
    def __init__(self):
        self.now = 10_000

    def __call__(self):
        return self.now


class FakeCall:
    def __init__(self):
        self.result = None
        self.closed = False
        self.close_hook = lambda: None

    def poll(self):
        return self.result

    def close(self):
        self.closed = True
        self.close_hook()


class FakeFactory:
    def __init__(self):
        self.calls = []
        self.inputs = []

    def __call__(self, text, caps):
        call = FakeCall()
        self.calls.append(call)
        self.inputs.append((text, caps))
        return call


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.caps = CapabilitySnapshot.from_metadata_json(CAP_PATH.read_text())
        self.clock = FakeClock()
        self.events = []
        self.factory = FakeFactory()
        self.sessions = []

    def tearDown(self):
        for session in self.sessions:
            session.close()

    def session(self, *, manual=False, **kwargs):
        options = dict(allow_shadow=True, allow_unqualified_metadata=True,
                       clock_ms=self.clock, emit=self.events.append)
        if not manual:
            options.update(mode="native-schema", worker_factory=self.factory)
        options.update(kwargs)
        session = ShadowSession(self.caps, **options)
        self.sessions.append(session)
        return session

    def outcomes(self, request_id=None):
        return [e for e in self.events if e["event"] == "outcome" and
                (request_id is None or e["request_id"] == request_id)]

    def test_explicit_opt_in_and_candidate_grant_required(self):
        with self.assertRaises(SessionError):
            ShadowSession(self.caps)
        session = self.session(manual=True, allow_unqualified_metadata=False)
        request_id = session.propose(velocity())
        self.assertEqual(self.outcomes(request_id)[-1]["state"], "rejected")
        self.assertIn("unqualified", self.outcomes(request_id)[-1]["reason"])

    def test_manual_is_clearly_labeled_not_model_inference_and_never_network(self):
        with patch("robot_llm.providers.post_completion", side_effect=AssertionError("network")), \
                patch("robot_llm.providers._load_baml", side_effect=AssertionError("BAML")):
            session = self.session(manual=True)
            request_id = session.propose(velocity())
            self.assertEqual(self.events[0]["evidence_kind"], "manual_proposal_not_model_inference")
            self.assertEqual(self.outcomes(request_id)[-1]["state"], "shadow_accepted")
            with self.assertRaises(SessionError):
                session.request("Move forward")
            self.clock.now += 100
            session.poll()
            self.assertEqual(self.outcomes(request_id)[-1]["state"], "expired")
            for event in self.events:
                self.assertFalse(event["physical_execution"])
                self.assertFalse(event["physical_stop_confirmed"])
                self.assertEqual(event["mode"], "SHADOW")
                self.assertNotEqual(event.get("state"), "succeeded")

    def test_host_issues_envelope_before_provider_with_current_snapshot(self):
        session = self.session()
        request_id = session.request("Forward at .02 m/s for .1 seconds")
        started = self.events[-1]
        self.assertEqual(started["event"], "request_started")
        self.assertEqual(started["envelope"]["issued_at_ms"], self.clock.now)
        self.assertEqual(started["envelope"]["expires_at_ms"], self.clock.now + 10000)
        self.assertEqual(started["envelope"]["capability_digest"], self.caps.digest)
        self.assertEqual(self.factory.inputs[0][1]["capability_digest"], self.caps.digest)
        self.factory.calls[0].result = ProviderResult(velocity(), 999999999)
        self.clock.now += 3
        session.poll()
        self.assertEqual(self.outcomes(request_id)[-1]["state"], "shadow_accepted")
        self.assertEqual(self.outcomes(request_id)[-1]["lease_expires_at_ms"], self.clock.now + 100)
        self.assertTrue(self.factory.calls[0].closed)

    def test_real_provider_adapter_with_injected_transport_uses_same_session_gate(self):
        payloads = []
        def transport(config, payload):
            payloads.append(payload)
            return {"choices": [{"finish_reason": "stop", "message": {
                "role": "assistant", "content": velocity()}}]}
        provider = InferenceProvider("native-schema", EndpointConfig("https://example.invalid/v1", "test"),
                                     transport=transport)
        def factory(text, caps):
            call = FakeCall()
            call.result = provider.generate(text, caps)
            return call
        session = self.session(worker_factory=factory)
        request_id = session.request("Forward .02 m/s for .1 s in body frame")
        session.poll()
        self.assertEqual(self.outcomes(request_id)[-1]["state"], "shadow_accepted")
        self.assertEqual(len(payloads), 1)
        self.assertFalse(payloads[0]["stream"])
        self.assertIn(self.caps.digest, payloads[0]["messages"][0]["content"])

    def test_latest_request_wins_pending_and_active(self):
        session = self.session()
        first = session.request("first")
        second = session.request("second")
        self.assertTrue(self.factory.calls[0].closed)
        self.assertEqual(self.outcomes(first)[-1]["state"], "cancelled")
        self.factory.calls[0].result = ProviderResult(velocity(), 0)
        self.factory.calls[1].result = ProviderResult(velocity(), 0)
        session.poll()
        self.assertEqual(self.outcomes(second)[-1]["state"], "shadow_accepted")
        third = session.request("third")
        self.assertEqual(self.outcomes(second)[-1]["state"], "cancelled")
        self.assertEqual(len(self.factory.calls), 3)
        self.assertEqual(self.events[-1]["request_id"], third)
        self.assertFalse(any(e["state"] == "shadow_accepted" for e in self.outcomes(first)))

    def test_cancel_available_while_pending_and_late_result_never_submitted(self):
        session = self.session()
        request_id = session.request("pending")
        session.cancel()
        self.factory.calls[0].result = ProviderResult(velocity(), 0)
        session.poll()
        self.assertTrue(self.factory.calls[0].closed)
        self.assertEqual([e["state"] for e in self.outcomes(request_id)], ["cancelled"])
        session.request("new request is allowed after cancel")

    def test_stop_latches_before_worker_cleanup_and_never_confirms_hardware(self):
        session = self.session()
        request_id = session.request("pending")
        def check_latched():
            self.assertEqual(self.outcomes()[-1]["state"], "stop_requested")
            self.assertFalse(self.outcomes()[-1]["physical_stop_confirmed"])
            with self.assertRaises(SessionError):
                session.request("must not start")
        self.factory.calls[0].close_hook = check_latched
        session.stop()
        self.assertEqual(self.outcomes(request_id)[-1]["state"], "cancelled")

    def test_model_stop_uses_same_latch_but_only_complete_response(self):
        session = self.session()
        session.request("stop")
        self.factory.calls[0].result = ProviderResult(STOP, 0)
        session.poll()
        self.assertEqual(self.outcomes()[-1]["state"], "stop_requested")
        with self.assertRaises(SessionError):
            session.request("another")

    def test_deadline_includes_real_elapsed_and_cleanup_not_provider_latency(self):
        session = self.session(request_ttl_ms=1000)
        request_id = session.request("pending")
        self.factory.calls[0].result = ProviderResult(velocity(), 0)
        self.factory.calls[0].close_hook = lambda: setattr(self.clock, "now", self.clock.now + 1000)
        session.poll()
        self.assertEqual(self.outcomes(request_id)[-1]["state"], "expired")

    def test_deadline_ends_worker_without_any_reply(self):
        session = self.session(request_ttl_ms=1000)
        request_id = session.request("pending")
        self.clock.now += 1000
        session.poll()
        self.assertTrue(self.factory.calls[0].closed)
        self.assertEqual(self.outcomes(request_id)[-1]["state"], "expired")
        session.poll()
        self.assertEqual(len(self.outcomes(request_id)), 1)

    def test_duration_must_fit_remaining_original_deadline(self):
        session = self.session(request_ttl_ms=1000)
        request_id = session.request("pending")
        self.clock.now += 901
        self.factory.calls[0].result = ProviderResult(velocity(), 0)
        session.poll()
        self.assertEqual(self.outcomes(request_id)[-1]["state"], "rejected")
        self.assertIn("deadline", self.outcomes(request_id)[-1]["reason"])

    def test_snapshot_change_cancels_pending_and_next_provider_uses_new_digest(self):
        session = self.session()
        request_id = session.request("first")
        new = replace(self.caps, max_duration_ms=500)
        session.replace_capabilities(new)
        self.assertTrue(self.factory.calls[0].closed)
        self.assertEqual(self.outcomes(request_id)[-1]["state"], "cancelled")
        session.request("second")
        self.assertEqual(self.factory.inputs[-1][1]["capability_digest"], new.digest)
        self.assertEqual(self.factory.inputs[-1][1]["max_duration_ms"], 500)

    def test_snapshot_change_reaps_worker_even_when_event_sink_breaks(self):
        session = self.session()
        request_id = session.request("pending")
        def broken(event):
            raise BrokenPipeError("event sink failed")
        session._emit = broken
        try:
            with self.assertRaises(BrokenPipeError):
                session.replace_capabilities(replace(self.caps, max_duration_ms=500))
            self.assertTrue(self.factory.calls[0].closed)
            self.assertEqual(session.supervisor.outcome(request_id, now_ms=self.clock.now).state,
                             OutcomeState.CANCELLED)
        finally:
            session._emit = self.events.append

    def test_invalid_original_is_never_rescued_by_parser_or_partial(self):
        for raw in ('{"schema_version":1,"kind":"velocity_request"',
                    '```json\n' + velocity() + '\n```',
                    '{"schema_version":1,"kind":"stop_request","kind":"stop_request"}'):
            with self.subTest(raw=raw):
                session = self.session()
                request_id = session.request("request")
                self.factory.calls[-1].result = ProviderResult(raw, 0, candidate_content=velocity(), repair_status="changed")
                session.poll()
                self.assertEqual(self.outcomes(request_id)[-1]["state"], "rejected")
                session.close()

    def test_changed_valid_parser_candidate_and_failures_cancel_without_retry(self):
        for result in (ProviderResult(velocity(), 0, candidate_content=velocity(forward_m_s=-.02)),
                       ProviderResult(velocity(), 0, repair_status="parse_error"),
                       ProviderResult(None, 0, error="SECRET http://endpoint.invalid/key"),
                       ProviderResult(None, 0, timed_out=True)):
            with self.subTest(result=result):
                session = self.session()
                before = len(self.factory.calls)
                request_id = session.request("request")
                self.factory.calls[-1].result = result
                session.poll()
                self.assertEqual(self.outcomes(request_id)[-1]["state"], "cancelled")
                self.assertEqual(len(self.factory.calls), before + 1)
                self.assertNotIn("SECRET", json.dumps(self.events))
                self.assertNotIn("endpoint.invalid", json.dumps(self.events))
                session.close()

    def test_malformed_command_cancels_and_does_not_echo_sensitive_input(self):
        for line in ('{"op":"request","text":"ignored","SECRET":1}',
                     '{"op":"stop","op":"request"}', '{"op":"request","text":true}',
                     '["stop"]', 'not json SECRET'):
            session = self.session()
            request_id = session.request("pending")
            self.assertTrue(session.handle_line(line))
            self.assertEqual(self.outcomes(request_id)[-1]["state"], "cancelled")
            self.assertTrue(self.factory.calls[-1].closed)
            self.assertEqual(self.events[-1]["event"], "command_error")
            self.assertNotIn("SECRET", json.dumps(self.events))
            session.close()

    def test_request_limit_prevents_unbounded_supervisor_history(self):
        session = self.session(manual=True, max_requests=2)
        session.propose(CLARIFY)
        session.propose(CLARIFY)
        with self.assertRaises(SessionError):
            session.propose(CLARIFY)

    def test_close_is_idempotent_and_clock_failure_still_cancels(self):
        session = self.session()
        request_id = session.request("pending")
        self.clock.now -= 1
        with self.assertRaises(SessionError):
            session.poll()
        session.close()
        session.close()
        self.assertTrue(self.factory.calls[0].closed)
        self.assertEqual(self.outcomes(request_id)[-1]["state"], "cancelled")
        self.assertEqual(len([e for e in self.events if e["event"] == "session_closed"]), 1)

    def test_concurrent_submit_and_stop_always_end_cancelled_or_stop_latched(self):
        for _ in range(20):
            session = self.session()
            request_id = session.request("pending")
            self.factory.calls[-1].result = ProviderResult(velocity(), 0)
            barrier = threading.Barrier(2)
            def action(method):
                barrier.wait()
                method()
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(action, session.poll), pool.submit(action, session.stop)]
                for future in futures:
                    future.result()
            self.assertEqual(session.supervisor.outcome(request_id, now_ms=self.clock.now).state,
                             OutcomeState.CANCELLED)
            self.assertEqual(self.outcomes(request_id)[-1]["state"], "cancelled")
            with self.assertRaises(SessionError):
                session.request("no")
            session.close()


class NonblockingIOTests(unittest.TestCase):
    def setUp(self):
        self.read_input, self.write_input = os.pipe()
        self.read_output, self.write_output = os.pipe()
        self.io = NonblockingJsonlIO(self.read_input, self.write_output)
        self.caps = CapabilitySnapshot.from_metadata_json(CAP_PATH.read_text())

    def tearDown(self):
        self.io.close()
        for fd in (self.read_input, self.write_input, self.read_output, self.write_output):
            os.close(fd)

    def test_duplicate_descriptors_restore_original_shared_blocking_flag(self):
        read_fd, write_fd = os.pipe()
        alias = os.dup(read_fd)
        try:
            self.assertTrue(os.get_blocking(read_fd))
            stream = NonblockingJsonlIO(read_fd, alias)
            self.assertFalse(os.get_blocking(read_fd))
            self.assertFalse(os.get_blocking(alias))
            stream.close()
            self.assertTrue(os.get_blocking(read_fd))
            self.assertTrue(os.get_blocking(alias))
        finally:
            os.close(alias)
            os.close(read_fd)
            os.close(write_fd)

    def test_split_unicode_lines_and_cancellable_idle_read(self):
        data = '{"op":"request","text":"go 🚀"}\n'.encode()
        os.write(self.write_input, data[:30])
        os.write(self.write_input, data[30:])
        self.assertEqual(self.io.readline(16386), data.decode())
        self.io.cancel_read()
        self.assertEqual(self.io.readline(16386), "")

    def test_fatal_monitor_error_wakes_idle_reader_and_reaps_worker(self):
        clock, factory = FakeClock(), FakeFactory()
        session = ShadowSession(self.caps, allow_shadow=True, mode="native-schema",
                                worker_factory=factory, clock_ms=clock, emit=self.io.emit)
        session.request("pending")
        clock.now -= 1
        start = time.monotonic()
        self.assertEqual(run_jsonl(session, self.io, poll_interval_s=.002), 2)
        self.assertLess(time.monotonic() - start, 1)
        self.assertTrue(factory.calls[0].closed)
        self.assertFalse(any(t.name == "robot-llm-shadow-monitor" for t in threading.enumerate()))

    def test_output_backpressure_fails_closed_and_reaps_pending_work(self):
        factory = FakeFactory()
        session = ShadowSession(self.caps, allow_shadow=True, mode="native-schema",
                                worker_factory=factory, emit=self.io.emit)
        session.request("pending")
        while True:
            try:
                os.write(self.write_output, b"x" * 4096)
            except BlockingIOError:
                break
        os.write(self.write_input, b'{"op":"stop"}\n')
        start = time.monotonic()
        self.assertEqual(run_jsonl(session, self.io), 2)
        self.assertLess(time.monotonic() - start, 1)
        self.assertTrue(factory.calls[0].closed)
        with self.assertRaises(SessionError):
            session.request("must stay stopped")


class ProcessAndCliTests(unittest.TestCase):
    def setUp(self):
        self.caps = CapabilitySnapshot.from_metadata_json(CAP_PATH.read_text())

    def wait_result(self, call):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            result = call.poll()
            if result is not None:
                return result
            time.sleep(.01)
        self.fail("local test child did not finish")

    def test_disposable_process_success_and_sanitized_failure(self):
        for target in (child_success, child_raises):
            with self.subTest(target=target):
                call = ProcessCall(target, ())
                try:
                    result = self.wait_result(call)
                    if target == child_success:
                        self.assertEqual(result.raw_content, CLARIFY)
                    else:
                        self.assertEqual(result.error, "provider_failed")
                finally:
                    call.close()
                self.assertTrue(call._closed)
                call.close()

    def test_native_and_python_diagnostics_never_escape_child(self):
        script = """
from robot_llm.session import ProcessCall
from robot_llm.tests.test_session import child_native_output
import time
if __name__ == '__main__':
    call = ProcessCall(child_native_output, ())
    try:
        deadline = time.monotonic() + 4
        result = None
        while result is None and time.monotonic() < deadline:
            result = call.poll()
            time.sleep(.01)
        assert result is not None
        print('received')
    finally:
        call.close()
"""
        completed = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                                   text=True, capture_output=True, timeout=5)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "received\n")
        self.assertEqual(completed.stderr, "")

    def test_interruption_after_process_start_reaps_worker(self):
        context = multiprocessing.get_context("spawn")
        original = context.Process.start
        def interrupted(process):
            original(process)
            raise KeyboardInterrupt()
        with patch.object(context.Process, "start", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                ProcessCall(child_hangs, ())
        self.assertFalse(any(p.name == "robot-llm-inference" for p in multiprocessing.active_children()))

    def test_broken_event_sink_still_reaps_worker_and_monitor(self):
        factory = FakeFactory()
        session = ShadowSession(self.caps, allow_shadow=True, mode="native-schema", worker_factory=factory)
        session.request("pending")
        def broken(event):
            raise BrokenPipeError("must not escape shutdown")
        session._emit = broken
        self.assertEqual(run_jsonl(session, io.StringIO("")), 2)
        self.assertTrue(factory.calls[0].closed)
        self.assertFalse(any(t.name == "robot-llm-shadow-monitor" for t in threading.enumerate()))

    def test_pending_process_stop_and_shutdown_reap_child(self):
        events, calls = [], []
        def factory(*_):
            call = ProcessCall(child_hangs, ())
            calls.append(call)
            return call
        session = ShadowSession(self.caps, allow_shadow=True, mode="native-schema",
                                worker_factory=factory, emit=events.append)
        session.request("pending")
        start = time.monotonic()
        session.stop()
        session.close()
        self.assertLess(time.monotonic() - start, 3)
        self.assertTrue(calls[0]._closed)
        self.assertFalse(any(p.name == "robot-llm-inference" for p in multiprocessing.active_children()))
        self.assertFalse(events[-1]["physical_execution"])

    def test_monitor_expires_without_new_input_and_eof_reaps_monitor(self):
        events = []
        session = ShadowSession(self.caps, allow_shadow=True, allow_unqualified_metadata=True,
                                emit=events.append, request_ttl_ms=100)
        class Input:
            count = 0
            def readline(self, limit):
                self.count += 1
                if self.count == 1:
                    return json.dumps({"op": "propose", "raw": velocity(duration_ms=20)}) + "\n"
                time.sleep(.08)
                return ""
        self.assertEqual(run_jsonl(session, Input(), poll_interval_s=.002), 0)
        states = [e.get("state") for e in events]
        self.assertIn("shadow_accepted", states)
        self.assertIn("expired", states)
        self.assertFalse(any(t.name == "robot-llm-shadow-monitor" for t in threading.enumerate()))

    def test_eof_keyboard_interrupt_and_oversize_input_cancel_pending(self):
        for stream, expected in ((io.StringIO(""), 0), (io.StringIO("x" * 17000), 2)):
            factory = FakeFactory()
            session = ShadowSession(self.caps, allow_shadow=True, mode="native-schema", worker_factory=factory)
            session.request("pending")
            self.assertEqual(run_jsonl(session, stream), expected)
            self.assertTrue(factory.calls[0].closed)
        class Interrupted:
            def readline(self, limit):
                raise KeyboardInterrupt()
        session = ShadowSession(self.caps, allow_shadow=True)
        self.assertEqual(run_jsonl(session, Interrupted()), 130)
        self.assertFalse(any(t.name == "robot-llm-shadow-monitor" for t in threading.enumerate()))

    def test_cli_offline_smoke_outputs_jsonl_without_network(self):
        commands = "\n".join(json.dumps(value) for value in
                             ({"op": "propose", "raw": velocity()}, {"op": "stop"}, {"op": "close"})) + "\n"
        completed = subprocess.run([sys.executable, "-m", "robot_llm.session", "--allow-shadow",
                                    "--allow-unqualified-metadata", "--capabilities", str(CAP_PATH)],
                                   cwd=ROOT, input=commands, text=True, capture_output=True, timeout=5)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stderr, "")
        rows = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual(rows[0]["evidence_kind"], "manual_proposal_not_model_inference")
        self.assertIn("shadow_accepted", [row.get("state") for row in rows])
        self.assertEqual(rows[-1]["event"], "session_closed")

    def test_real_cli_exits_under_unread_stdout_backpressure(self):
        process = subprocess.Popen([sys.executable, "-m", "robot_llm.session", "--allow-shadow",
                                    "--capabilities", str(CAP_PATH)], cwd=ROOT,
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            # Keep stdin open and do not drain stdout until the process has exited.
            process.stdin.write(b'{"op":"status"}\n' * 1000)
            process.stdin.flush()
            process.wait(timeout=3)
            self.assertEqual(process.returncode, 2)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdin.close()
            process.stdout.close()
            process.stderr.close()

    def test_live_flags_required_and_no_configuration_secrets_echoed(self):
        for args in (("--mode", "native-schema"),
                     ("--allow-network", "--base-url", "https://secret.example/v1"),
                     ("--mode", "native-schema", "--allow-network", "--base-url", "https://user:SECRET@example.invalid/v1", "--model", "test")):
            with patch("sys.stderr", new_callable=io.StringIO) as error, \
                    patch("robot_llm.session.ProcessCall", side_effect=AssertionError("network work")):
                self.assertEqual(main(["--capabilities", str(CAP_PATH), "--allow-shadow", *args]), 2)
                self.assertNotIn("SECRET", error.getvalue())
                self.assertNotIn("secret.example", error.getvalue())
        with self.assertRaises(SessionError):
            LiveInference("native-schema", EndpointConfig("https://example.invalid/v1", "model"))


if __name__ == "__main__":
    unittest.main()
