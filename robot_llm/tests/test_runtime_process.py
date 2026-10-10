"""Adversarial local pipe/process tests; no JVM, actors, network or devices."""
import os
import sys
import threading
import time
import unittest
from unittest.mock import patch
import json
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

from robot_llm.pixel_runtime import PixelMathProcess
from robot_llm.runtime_bridge import BridgeError
from robot_llm.runtime_demo import DEFAULT_ASSETS, _run_preparation, math_command


class RuntimeProcessTests(unittest.TestCase):
    def peer(self, code, timeout=.2):
        peer = PixelMathProcess([sys.executable, '-u', '-c', code], timeout_s=timeout)
        self.addCleanup(peer.close)
        return peer

    def test_unread_input_is_bounded_even_with_unsolicited_output(self):
        peer = self.peer("import time; print('{}',flush=True); print('{}',flush=True); time.sleep(60)")
        started = time.monotonic()
        with self.assertRaises(BridgeError):
            peer.exchange({'padding': 'x' * 60000})
            peer.exchange({'padding': 'x' * 60000})
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(peer._reader.is_alive())
        self.assertFalse(any(writer.is_alive() for writer in peer._writers))

    def test_silent_peer_response_timeout_reaps_worker(self):
        peer = self.peer("import sys,time; sys.stdin.readline(); time.sleep(60)")
        with self.assertRaises(BridgeError):
            peer.exchange({'op': 'reset'})
        self.assertIsNotNone(peer._process.poll())
        self.assertFalse(peer._reader.is_alive())

    @unittest.skipUnless(os.name == 'posix', 'POSIX process-group cleanup test')
    def test_wrapper_child_inheriting_stdout_is_terminated(self):
        peer = self.peer("import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); print('{}',flush=True); time.sleep(60)")
        peer.exchange({'op': 'reset'})
        started = time.monotonic()
        peer.close()
        self.assertLess(time.monotonic() - started, 3)
        self.assertFalse(peer._reader.is_alive())

    def test_invalid_partial_and_oversized_responses_fail_closed(self):
        for code in ("print('broken',flush=True)", "print('{}',end='',flush=True)",
                     "print('x'*70000,flush=True)", "print('{\"error\":\"secret\"}',flush=True)"):
            with self.subTest(code=code):
                peer = self.peer(code)
                with self.assertRaisesRegex(BridgeError, '^math_process_failed$'):
                    peer.exchange({'op': 'reset'})

    def test_jsonl_exchange_and_idempotent_close(self):
        peer = self.peer("import sys,json; print(json.dumps({'seen':json.loads(sys.stdin.readline())['op']}),flush=True)")
        self.assertEqual(peer.exchange({'op':'reset'}), {'seen':'reset'})
        peer.close()
        peer.close()
        with self.assertRaises(BridgeError):
            peer.exchange({'op':'reset'})

    def test_close_during_wait_fails_without_threads_remaining(self):
        peer = self.peer("import time; time.sleep(60)", timeout=2)
        failures = []
        def exchange():
            try:
                peer.exchange({'op':'reset'})
            except BridgeError:
                failures.append(True)
        thread = threading.Thread(target=exchange)
        thread.start()
        time.sleep(.05)
        peer.close()
        thread.join(3)
        self.assertEqual(failures, [True])
        self.assertFalse(thread.is_alive())

    def test_cold_preparation_has_separate_bound_and_no_download_flags(self):
        java = shutil.which('java') or '/unavailable/java'
        command = [str(Path(java).resolve()), '-cp', '/verified/classes',
                   'com.leo.pixelrobot.host.PolicyFrameBridgeKt', str(DEFAULT_ASSETS)]
        report = dict(status='bridge_ready', hardware_access=False, command=command)
        with patch('robot_llm.runtime_demo.shutil.which', return_value=java), \
             patch('robot_llm.runtime_demo._run_preparation', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(report))) as run:
            self.assertEqual(math_command(), command)
            self.assertIn('--bridge-command', run.call_args.args[0])
            self.assertNotIn('--download', run.call_args.args[0])
            self.assertEqual(run.call_args.kwargs['timeout_s'], 240)

    def test_cold_preparation_failure_cannot_launch_unverified_command(self):
        for stdout in ('[]', '{}', '{"status":"bridge_ready","hardware_access":true,"command":[]}'):
            with patch('robot_llm.runtime_demo._run_preparation', return_value=SimpleNamespace(returncode=0, stdout=stdout)):
                with self.assertRaisesRegex(BridgeError, '^math_preparation_failed_or_blocked$'):
                    math_command()
        with patch('robot_llm.runtime_demo._run_preparation', side_effect=subprocess.TimeoutExpired('compiler', 240)):
            with self.assertRaisesRegex(BridgeError, '^math_preparation_failed_or_blocked$'):
                math_command()

    @unittest.skipUnless(os.name == 'posix', 'POSIX cold-compiler process-group cleanup')
    def test_cold_preparation_timeout_terminates_descendants(self):
        command = [sys.executable, '-c', "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); time.sleep(60)"]
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_preparation(command, timeout_s=.1)
        self.assertLess(time.monotonic() - started, 3)


if __name__ == '__main__':
    unittest.main()
