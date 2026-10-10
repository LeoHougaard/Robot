"""Runner failure semantics; no model, backend, package install or robot access."""
from __future__ import annotations

import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from validation import checks, runner


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_aggregate_requires_all_requested_passes(self):
        self.assertEqual(runner.aggregate([]), "BLOCKED")
        for bad in ("FAIL", "BLOCKED", "TIMEOUT", "SKIPPED"):
            with self.subTest(bad=bad):
                self.assertNotEqual(runner.aggregate([{"status": "PASS"}, {"status": bad}]), "PASS")
        self.assertEqual(runner.aggregate([{"status": "PASS"}, {"status": "SKIPPED", "required": False}]), "PASS")

    def test_commands_have_no_shell_interpolation(self):
        output = self.root / "output.log"
        result = runner.execute_command([sys.executable, "-c", "import sys; print(sys.argv[1])", "x; echo unsafe"],
            cwd=self.root, log=output, timeout=10)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(output.read_text().strip(), "x; echo unsafe")

    def test_nonzero_exit_is_failure(self):
        result = runner.execute_command([sys.executable, "-c", "raise SystemExit(7)"],
            cwd=self.root, log=self.root / "out.log", timeout=10)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["returncode"], 7)

    def test_timeout_is_not_pass(self):
        result = runner.execute_command([sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=self.root, log=self.root / "out.log", timeout=.05)
        self.assertEqual(result["status"], "TIMEOUT")
        self.assertLess(result["duration_seconds"], 10)

    def test_parent_exit_does_not_leak_descendant_or_pass(self):
        if os.name != "posix":
            self.assertFalse(runner.cleanup_descendants(None))
            return
        script = "import subprocess,sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])"
        result = runner.execute_command([sys.executable, "-c", script], cwd=self.root,
                                        log=self.root / "child.log", timeout=5)
        self.assertEqual(result["status"], "FAIL")
        self.assertIn("leaked child", result["reason"])

    def test_missing_executable_is_blocked(self):
        result = runner.execute_command([str(self.root / "nonexistent-python")],
            cwd=self.root, log=self.root / "out.log", timeout=1)
        self.assertEqual(result["status"], "BLOCKED")

    def test_missing_interpreter_probe_is_blocked(self):
        self.assertEqual(runner.probe_python(str(self.root / "nonexistent"))["status"], "BLOCKED")

    def test_dependencies_require_exact_public_version(self):
        step = runner.Step("test", needs=("baml-py==0.226.2",))
        for packages in ({}, {"baml-py": "0.226.1"}):
            self.assertIsNotNone(runner.dependency_problem(step, {"status": "PASS", "packages": packages}))
        self.assertIsNone(runner.dependency_problem(step,
            {"status": "PASS", "packages": {"baml-py": "0.226.2"}}))
        torch = runner.Step("torch", needs=("torch==2.5.1",))
        self.assertIsNone(runner.dependency_problem(torch,
            {"status": "PASS", "packages": {"torch": "2.5.1+cpu"}}))

    def test_secret_and_network_environment_not_forwarded(self):
        with patch.dict(os.environ, {"ROBOT_LLM_API_KEY": "secret", "BOUNDARY_API_KEY": "secret",
                "HTTPS_PROXY": "secret", "PYTHONPATH": "secret", "PYTEST_ADDOPTS": "secret",
                "NODE_OPTIONS": "--require untrusted-preload.js", "NODE_PATH": "untrusted-modules"}):
            env = runner.clean_environment()
        for key in ("ROBOT_LLM_API_KEY", "BOUNDARY_API_KEY", "HTTPS_PROXY", "PYTHONPATH", "PYTEST_ADDOPTS",
                    "NODE_OPTIONS", "NODE_PATH"):
            self.assertNotIn(key, env)
        self.assertEqual(env["CUDA_VISIBLE_DEVICES"], "")
        self.assertEqual(env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"], "1")

    def result(self, value):
        path = self.root / "result.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_structured_report_parse(self):
        expected = {"schema_version": 1, "check": "x", "status": "PASS", "details": {"run": 10}}
        self.assertEqual(runner.read_check_result(self.result(expected), "x"), expected)

    def test_invalid_structured_report_cannot_pass(self):
        for value in ({}, {"schema_version": 1, "check": "x", "status": "PASS"},
                      {"schema_version": 1, "check": "other", "status": "PASS", "details": {}},
                      {"schema_version": 1, "check": "x", "status": "maybe", "details": {}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                runner.read_check_result(self.result(value), "x")
        with self.assertRaises(ValueError): runner.read_check_result(self.root / "missing.json", "x")

    def test_duplicate_and_nonfinite_report_rejected(self):
        path = self.root / "bad.json"
        for text in ('{"status":"FAIL","status":"PASS"}', '{"value":NaN}'):
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError): runner.read_check_result(path, "x")

    def test_plan_is_deterministic_and_generates_before_baml_tests(self):
        interpreters = {key: "python" for key in ("base", "baml", "torch", "runtime")}
        left = runner.build_plan("offline", interpreters)
        self.assertEqual(left, runner.build_plan("offline", interpreters))
        selected = runner.build_plan("offline", interpreters, selected=("baml-unit",))
        self.assertEqual([row["name"] for row in selected], ["baml-generate", "baml-unit"])
        self.assertTrue(all(row["timeout"] > 0 for row in left))
        with self.assertRaises(ValueError): runner.build_plan("offline", interpreters, selected=("unknown",))

    def test_core_has_no_optional_dependency_steps(self):
        plan = runner.build_plan("core", {key: "python" for key in ("base", "baml", "torch", "runtime")})
        self.assertTrue(all(row["interpreter"] == "base" and not row["needs"] for row in plan))
        self.assertNotIn("cpu-torch", [row["name"] for row in plan])
        self.assertNotIn("pixel-web-ui", [row["name"] for row in plan])

    def init_git(self):
        def git(*args):
            subprocess.run(["git", "-C", str(self.root), *args], check=True,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=runner.clean_environment())
        git("init", "-q")
        (self.root / "source.py").write_text("value = 1\n", encoding="utf-8")
        (self.root / ".gitignore").write_text("results/\n.venv/\n", encoding="utf-8")
        git("add", ".")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
        return git

    def test_source_snapshot_detects_edit_added_deleted_and_head(self):
        git = self.init_git()
        before = runner.source_snapshot(self.root)
        self.assertEqual(before["sha256"], runner.source_snapshot(self.root)["sha256"])
        (self.root / "source.py").write_text("value = 2\n", encoding="utf-8")
        changed = runner.source_snapshot(self.root)
        self.assertNotEqual(before["sha256"], changed["sha256"])
        (self.root / "new.py").write_text("x = 3", encoding="utf-8")
        self.assertNotEqual(changed["sha256"], runner.source_snapshot(self.root)["sha256"])
        (self.root / "source.py").unlink()
        self.assertTrue(any(row["kind"] == "deleted" for row in runner.source_snapshot(self.root)["files"]))
        git("add", ".")
        git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "changed")
        self.assertNotEqual(before["head"], runner.source_snapshot(self.root)["head"])

    def test_generated_evidence_and_venvs_do_not_change_snapshot(self):
        self.init_git()
        before = runner.source_snapshot(self.root)
        for name in ("results", ".venv"):
            (self.root / name).mkdir()
            (self.root / name / "data.txt").write_text("generated")
        self.assertEqual(before, runner.source_snapshot(self.root))
        extra = self.root / "custom-evidence"
        extra.mkdir()
        (extra / "report.json").write_text("{}")
        self.assertEqual(before, runner.source_snapshot(self.root, exclude=(extra,)))

    def test_python_socket_guard_denies_before_dns(self):
        path = self.root / "guard.json"
        result = runner.execute_command([sys.executable, "-m", "validation.checks", "offline-guard-probe",
            "--result", str(path), "--artifact", str(self.root / "artifact.json"),
            "--manifest", str(self.root / "manifest.json")], cwd=runner.ROOT,
            log=self.root / "guard.log", timeout=15)
        self.assertEqual(result["status"], "PASS", (self.root / "guard.log").read_text())
        self.assertTrue(runner.read_check_result(path, "offline-guard-probe")["details"]["denied_before_dns"])

    def test_cli_plan_never_starts_checks_or_probes(self):
        with patch.object(runner, "execute_command", side_effect=AssertionError("no execution")), \
                patch.object(runner, "probe_python", side_effect=AssertionError("no probe")), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(runner.main(["--plan"]), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual(plan["profile"], "offline")
        self.assertEqual(len(plan["steps"]), len(runner.STEPS))
        self.assertFalse(any("allow-network" in str(row) for row in plan["steps"]))

    def test_cli_rejects_network_flags(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            runner.main(["--allow-network"])
        self.assertEqual(raised.exception.code, 2)

    def test_fixture_gate_preserves_deliberate_negative_cases(self):
        status, details = checks.fixture_check(self.root / "fixtures.json")
        self.assertEqual(status, "PASS")
        self.assertEqual(details["summary"]["unsafe_accept_count"], 1)
        self.assertIn("test doubles", details["scope"])

    def test_unit_skip_is_blocked_and_empty_suite_fails(self):
        empty = unittest.TestSuite()
        with patch.object(checks.unittest.TestLoader, "loadTestsFromNames", return_value=empty), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(checks.unit_suite(".", "", 1, ("missing",))[0], "FAIL")
        class Skipped(unittest.TestCase):
            @unittest.skip("not installed")
            def test_skip(self): pass
        with patch.object(checks.unittest.TestLoader, "loadTestsFromNames", return_value=unittest.defaultTestLoader.loadTestsFromTestCase(Skipped)), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(checks.unit_suite(".", "", 1, ("fake",))[0], "BLOCKED")

    def test_atomic_json_roundtrip(self):
        path = self.root / "nested/report.json"
        runner.write_json(path, {"status": "PASS", "x": 1})
        self.assertEqual(json.loads(path.read_text()), {"status": "PASS", "x": 1})
        self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_human_summary_never_implies_physical_success(self):
        summary = runner.human_summary({"status": "PASS", "profile": "core", "cycle": 1,
                                      "partial_selection": True, "steps": []})
        self.assertIn("full offline validation was not requested", summary)
        self.assertIn("No physical safety", summary)

    def cycle_args(self, profile="core", steps=("fixtures",)):
        args = runner.parser().parse_args(["--profile", profile])
        args.root, args.output = self.root, self.root / "evidence"
        args.steps = steps
        args.interpreters = {key: sys.executable for key in ("base", "baml", "torch", "runtime")}
        return args

    @staticmethod
    def snapshot(identity):
        return {"head": "head", "base": "base", "sha256": identity, "file_count": 1,
                "files": [], "scope": "test fixture"}

    def fake_check(self, command, **kwargs):
        result = Path(command[command.index("--result") + 1])
        runner.write_json(result, {"schema_version": 1, "check": command[3], "status": "PASS", "details": {}})
        return {"status": "PASS", "returncode": 0}

    def test_source_change_midrun_blocks_a_passing_check(self):
        args = self.cycle_args()
        with patch.object(runner, "source_snapshot", side_effect=[self.snapshot("before"), self.snapshot("after")]), \
                patch.object(runner, "probe_python", return_value={"status": "PASS", "packages": {}}), \
                patch.object(runner, "execute_command", side_effect=self.fake_check), \
                contextlib.redirect_stdout(io.StringIO()):
            report = runner.run_cycle(args, 1)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertFalse(report["all_requested_gates_passed"])
        self.assertEqual(report["steps"][0]["status"], "PASS")
        self.assertEqual(json.loads((args.output / "latest.json").read_text())["status"], "BLOCKED")

    def test_dependency_change_midrun_blocks_passing_check(self):
        args = self.cycle_args()
        with patch.object(runner, "source_snapshot", return_value=self.snapshot("same")), \
                patch.object(runner, "probe_python", side_effect=[{"status": "PASS", "packages": {}},
                                                                 {"status": "PASS", "packages": {"new": "1"}}]), \
                patch.object(runner, "execute_command", side_effect=self.fake_check), \
                contextlib.redirect_stdout(io.StringIO()):
            report = runner.run_cycle(args, 1)
        self.assertEqual(report["status"], "BLOCKED")

    def test_exit_zero_without_structured_result_is_failure(self):
        args = self.cycle_args()
        with patch.object(runner, "source_snapshot", return_value=self.snapshot("same")), \
                patch.object(runner, "probe_python", return_value={"status": "PASS", "packages": {}}), \
                patch.object(runner, "execute_command", return_value={"status": "PASS", "returncode": 0}), \
                contextlib.redirect_stdout(io.StringIO()):
            report = runner.run_cycle(args, 1)
        self.assertEqual(report["status"], "FAIL")
        self.assertIn("structured result", report["steps"][0]["reason"])

    def test_missing_pinned_dependency_blocks_without_execution(self):
        args = self.cycle_args("offline", ("baml-unit",))
        with patch.object(runner, "source_snapshot", return_value=self.snapshot("same")), \
                patch.object(runner, "probe_python", return_value={"status": "PASS", "packages": {}}), \
                patch.object(runner, "execute_command", side_effect=AssertionError("must not run")), \
                contextlib.redirect_stdout(io.StringIO()):
            report = runner.run_cycle(args, 1)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual([row["status"] for row in report["steps"][:2]], ["BLOCKED", "BLOCKED"])

    def test_watch_stops_only_when_requested_gates_pass(self):
        output = self.root / "watch"
        reports = [{"status": "BLOCKED", "all_requested_gates_passed": False},
                   {"status": "PASS", "all_requested_gates_passed": True}]
        with patch.object(runner, "run_cycle", side_effect=reports) as run, \
                patch.object(runner, "fingerprint", side_effect=["before", "after", "after"]), \
                contextlib.redirect_stdout(io.StringIO()):
            code = runner.main(["--profile", "core", "--watch", "--max-cycles", "3", "--interval", ".001",
                                "--output", str(output)])
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 2)
        loop = json.loads((output / "loop.json").read_text())
        self.assertEqual(loop["stop_reason"], "all_requested_gates_passed")
        self.assertEqual(len(loop["cycles"]), 2)

    def test_watch_maxcycles_does_not_turn_failure_into_success(self):
        output = self.root / "watch"
        with patch.object(runner, "run_cycle", return_value={"status": "FAIL", "all_requested_gates_passed": False}), \
                patch.object(runner, "fingerprint", return_value="unchanged"), \
                contextlib.redirect_stdout(io.StringIO()):
            code = runner.main(["--watch", "--max-cycles", "1", "--output", str(output)])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads((output / "loop.json").read_text())["stop_reason"], "max_cycles_reached")

    def test_watch_timeout_on_unchanged_failure_is_explicit(self):
        output = self.root / "watch"
        with patch.object(runner, "run_cycle", return_value={"status": "BLOCKED", "all_requested_gates_passed": False}), \
                patch.object(runner, "fingerprint", return_value="unchanged"), \
                contextlib.redirect_stdout(io.StringIO()):
            code = runner.main(["--watch", "--watch-timeout", ".005", "--interval", ".001", "--output", str(output)])
        self.assertEqual(code, 3)
        self.assertEqual(json.loads((output / "loop.json").read_text())["status"], "TIMEOUT")

    def test_external_profiles_never_dispatch_hardware(self):
        for profile in ("isaac", "android", "firmware", "hardware"):
            with self.subTest(profile=profile):
                self.assertEqual(runner.build_plan(profile, {"base": "python"}), [])
        live = runner.build_plan("live-model", {"base": "python"})
        self.assertEqual([item["name"] for item in live], ["live-model"])
        self.assertNotIn("evaluate", str(live))

    def test_xpass_without_reason_cannot_pass_cpu_gate(self):
        from types import SimpleNamespace
        evidence = checks.PytestEvidence()
        evidence.pytest_runtest_logreport(SimpleNamespace(nodeid="empty_reason_xpass", failed=False,
            skipped=False, passed=True, when="call", wasxfail=""))
        self.assertEqual(evidence.skipped, ["empty_reason_xpass"])

    def test_pixel_capture_is_explicitly_separate_from_host_tools(self):
        plan = runner.build_plan("offline", {key: sys.executable for key in ("base", "baml", "torch", "runtime")})
        self.assertIn("pixel-tools-unit", [row["name"] for row in plan])
        self.assertNotIn("pixel-capture", [row["name"] for row in plan])
        capture = runner.build_plan("pixel-capture", {"base": sys.executable})
        self.assertEqual([row["name"] for row in capture], ["pixel-capture"])
        self.assertEqual(checks.PIXEL_CAPTURE_TEST,
            "test_fit_sim_from_run_data.FitSimFromRunDataTest.test_downloaded_walk_matches_calibration_golden")

    def test_partition_records_exclusion_without_pretending_it_ran(self):
        class Sample(unittest.TestCase):
            def runTest(self): pass
        safe, external = Sample(), Sample()
        safe.id = lambda: "sample.safe"
        external.id = lambda: checks.PIXEL_CAPTURE_TEST
        with patch.object(checks.unittest.TestLoader, "loadTestsFromNames",
                          return_value=unittest.TestSuite([safe, external])), \
                contextlib.redirect_stderr(io.StringIO()):
            status, details = checks.unit_suite(".", "", 1, ("sample",), (), (checks.PIXEL_CAPTURE_TEST,))
        self.assertEqual(status, "PASS")
        self.assertEqual((details["discovered"], details["collected"], details["run"]), (2, 1, 1))
        self.assertEqual(details["skipped"], 0)
        self.assertEqual(details["excluded_external_tests"], [checks.PIXEL_CAPTURE_TEST])

    def test_partition_fails_if_expected_external_test_disappears(self):
        class Sample(unittest.TestCase):
            def runTest(self): pass
        with patch.object(checks.unittest.TestLoader, "loadTestsFromNames",
                          return_value=unittest.TestSuite([Sample()])), \
                contextlib.redirect_stderr(io.StringIO()):
            status, details = checks.unit_suite(".", "", 1, ("sample",), (), (checks.PIXEL_CAPTURE_TEST,))
        self.assertEqual(status, "FAIL")
        self.assertEqual(details["unknown_exclusions"], [checks.PIXEL_CAPTURE_TEST])

    def test_missing_original_capture_blocks_its_requested_gate(self):
        args = self.cycle_args("pixel-capture", ())
        with patch.object(runner, "source_snapshot", return_value=self.snapshot("same")), \
                patch.object(runner, "probe_python", return_value={"status": "PASS", "packages": {}}), \
                patch.object(runner, "execute_command", side_effect=AssertionError("capture must not run")), \
                contextlib.redirect_stdout(io.StringIO()):
            report = runner.run_cycle(args, 1)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(report["outstanding"]["pixel-capture"]["status"], "BLOCKED")
        self.assertIn("absent", report["steps"][0]["reason"])

    def test_capture_changes_are_fingerprinted_even_when_gitignored(self):
        args = self.cycle_args("pixel-capture", ())
        capture = args.root / runner.PIXEL_CAPTURE_PATH
        capture.parent.mkdir(parents=True)
        capture.write_text("original")
        before = runner.capture_snapshot(args)
        capture.write_text("different")
        self.assertNotEqual(before["sha256"], runner.capture_snapshot(args)["sha256"])
        args.profile = "offline"
        self.assertEqual(runner.capture_snapshot(args), {})

    @staticmethod
    def host_result():
        return {"schema_version": 1, "scope": "pixel-production-kotlin-host-unit-tests",
            "status": "passed", "test_count": 10, "ignored": 0, "assumption_failures": 0, "failures": [],
            "compiled_production_sources": ["Production.kt"], "source_fingerprint": "a" * 64, "java_version": "OpenJDK 17",
            "test_classes": ["ExampleTest"], "test_ids": [f"ExampleTest.test_{i}" for i in range(10)],
            "finished_test_ids": [f"ExampleTest.test_{i}" for i in range(10)],
            "hardware_access": False, "android_build_verified": False}

    def test_host_pass_remains_software_only(self):
        status, details = checks.validate_host_report(self.host_result(), returncode=0,
            scope="pixel-production-kotlin-host-unit-tests", minimum=10)
        self.assertEqual(status, "PASS")
        self.assertIs(details["hardware_access"], False)
        self.assertIs(details["android_build_verified"], False)

    def test_host_zero_exit_cannot_hide_incomplete_or_widened_evidence(self):
        for update in ({"status": "setup_complete"}, {"test_count": 0}, {"test_count": True},
                {"ignored": 1}, {"assumption_failures": 1}, {"assumption_failures": None}, {"failures": ["failed"]}, {"scope": "android-verified"},
                {"hardware_access": True}, {"android_build_verified": True},
                {"compiled_production_sources": []}, {"source_fingerprint": ""}, {"java_version": ""},
                {"test_ids": []}, {"test_ids": ["duplicate"] * 10}, {"finished_test_ids": []}, {"test_classes": None}):
            with self.subTest(update=update):
                report = dict(self.host_result(), **update)
                self.assertEqual(checks.validate_host_report(report, returncode=0,
                    scope="pixel-production-kotlin-host-unit-tests", minimum=10)[0], "FAIL")

    def test_required_jvm_classes_must_be_present_and_actually_executed(self):
        report = self.host_result()
        for classes in ([], ["AddedTest"]):
            report["test_classes"] = classes
            self.assertEqual(checks.validate_host_report(report, returncode=0,
                scope="pixel-production-kotlin-host-unit-tests", minimum=10,
                required_classes=("AddedTest",))[0], "FAIL")

    def test_missing_or_wrong_node_version_is_blocked(self):
        with patch.object(runner.shutil, "which", return_value=None):
            self.assertEqual(runner.probe_node("node")["status"], "BLOCKED")
        for version, status in (("v" + runner.NODE_VERSION, "PASS"), ("v22.1.0", "BLOCKED")):
            completed = subprocess.CompletedProcess([], 0, version + "\n", "")
            with patch.object(runner.shutil, "which", return_value=sys.executable), \
                    patch.object(runner.subprocess, "run", return_value=completed):
                self.assertEqual(runner.probe_node("node")["status"], status)

    def test_node_install_or_binary_change_updates_toolchain_fingerprint(self):
        args = self.cycle_args("offline", ("pixel-web-ui",))
        with patch.object(runner, "probe_node", side_effect=[{"status": "BLOCKED"},
                {"status": "PASS", "binary_sha256": "a"}, {"status": "PASS", "binary_sha256": "b"}]):
            missing, installed, changed = [runner.toolchain_snapshot(args) for _ in range(3)]
        self.assertNotEqual(missing, installed)
        self.assertNotEqual(installed, changed)

    def test_missing_node_blocks_without_executing_ui_script(self):
        args = self.cycle_args("offline", ("pixel-web-ui",))
        with patch.object(runner, "source_snapshot", return_value=self.snapshot("same")), \
                patch.object(runner, "probe_python", return_value={"status": "PASS", "packages": {}}), \
                patch.object(runner, "probe_node", return_value={"status": "BLOCKED", "reason": "Node missing"}), \
                patch.object(runner, "execute_command", side_effect=AssertionError("must not run")), \
                contextlib.redirect_stdout(io.StringIO()):
            report = runner.run_cycle(args, 1)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("Node", report["steps"][0]["reason"])

    @staticmethod
    def web_ui_result():
        return {"schema_version": 1, "scope": "pixel-production-shadow-panel-dom-stub-tests",
            "node_version": "v" + runner.NODE_VERSION, "status": "passed", "test_count": 40,
            "test_ids": [f"test-{i}" for i in range(40)], "passed_test_ids": [f"test-{i}" for i in range(40)],
            "skipped": 0, "todo": 0, "cancelled": 0, "failures": [],
            "production_html_path": "pixel_robot/app/src/main/assets/robot_control.html",
            "production_html_sha256": "a" * 64, "production_script_sha256": "b" * 64,
            "test_source_sha256": "c" * 64, "network_access": False, "dom_environment": "stub",
            "android_webview_verified": False, "hardware_access": False, "live_control_script_executed": False}

    def ui_validate(self, report, returncode=0):
        expected = {key: value for key, value in self.web_ui_result().items() if key.endswith("_sha256")}
        return checks.validate_web_ui_report(report, returncode=returncode, expected_digests=expected, minimum=40)

    def test_ui_pass_keeps_dom_stub_and_source_scope(self):
        status, details = self.ui_validate(self.web_ui_result())
        self.assertEqual(status, "PASS")
        self.assertFalse(details["android_webview_verified"])
        self.assertEqual(details["dom_environment"], "stub")
        self.assertEqual(details["test_count"], 40)

    def test_ui_empty_partial_duplicate_skipped_todo_and_cancelled_do_not_pass(self):
        for update in ({"test_count": True}, {"test_count": 0}, {"test_ids": []},
                {"test_ids": ["same"] * 40}, {"passed_test_ids": []}, {"skipped": 1},
                {"todo": 1}, {"cancelled": 1}, {"failures": ["failed"]}, {"skipped": False}):
            with self.subTest(update=update):
                self.assertEqual(self.ui_validate(dict(self.web_ui_result(), **update))[0], "FAIL")

    def test_ui_stale_source_or_unverified_runtime_claim_does_not_pass(self):
        for update in ({"production_html_sha256": "z" * 64}, {"production_script_sha256": ""},
                {"test_source_sha256": ""}, {"node_version": "v1.0.0"}, {"network_access": True},
                {"hardware_access": True}, {"android_webview_verified": True}, {"dom_environment": "browser"},
                {"live_control_script_executed": True},
                {"scope": "android-device-tested"}, {"status": "failed"}):
            with self.subTest(update=update):
                self.assertEqual(self.ui_validate(dict(self.web_ui_result(), **update))[0], "FAIL")
        self.assertEqual(self.ui_validate(self.web_ui_result(), returncode=1)[0], "FAIL")

    def test_ui_source_hash_preserves_crlf_and_requires_unique_marker(self):
        html = self.root / "pixel_robot/app/src/main/assets/robot_control.html"
        script = self.root / "pixel_robot/tools/test_shadow_ui.cjs"
        html.parent.mkdir(parents=True); script.parent.mkdir(parents=True)
        marker = b"// This panel never calls"
        html.write_bytes(b"<script>" + marker + b"\r\n(() => {})();\r\n</script>")
        script.write_bytes(b"fixture")
        before = checks.shadow_script_digests(self.root)
        html.write_bytes(html.read_bytes().replace(b"\r\n", b"\n"))
        self.assertNotEqual(before["production_script_sha256"], checks.shadow_script_digests(self.root)["production_script_sha256"])
        html.write_bytes(marker + marker + b"</script>")
        with self.assertRaises(ValueError): checks.shadow_script_digests(self.root)

    def test_ui_source_change_during_child_blocks_even_exit_zero(self):
        with patch.object(checks, "shadow_script_digests", side_effect=[{"a": "old"}, {"a": "new"}]), \
                patch.object(checks.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)):
            status, _ = checks.pixel_web_ui_check(self.root / "report.json", ["--node", "node"])
        self.assertEqual(status, "BLOCKED")

    def test_missing_host_dependencies_stay_blocked(self):
        report = dict(self.host_result(), status="blocked", test_count=0, error="Java unavailable")
        self.assertEqual(checks.validate_host_report(report, returncode=2,
            scope="pixel-production-kotlin-host-unit-tests", minimum=10)[0], "BLOCKED")
        self.assertEqual(checks.validate_host_report(report, returncode=0,
            scope="pixel-production-kotlin-host-unit-tests", minimum=10)[0], "FAIL")

    def test_jvm_cache_install_or_change_updates_watch_fingerprint(self):
        args = self.cycle_args("offline", ("pixel-jvm",))
        manifest = args.root / "pixel_robot/tools/host_kotlin/dependencies.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(json.dumps([{"file": "fixture.jar", "sha256": "a" * 64}]))
        with patch.object(runner.shutil, "which", return_value=None):
            missing = runner.toolchain_snapshot(args)
            cache = args.root / "pixel_robot/.tooling/host-kotlin"
            cache.mkdir(parents=True)
            (cache / "fixture.jar").write_bytes(b"installed")
            installed = runner.toolchain_snapshot(args)
            (cache / "fixture.jar").write_bytes(b"changed")
            changed = runner.toolchain_snapshot(args)
        self.assertIsNone(missing["jars"]["fixture.jar"]["sha256"])
        self.assertNotEqual(missing, installed)
        self.assertNotEqual(installed, changed)

    def test_python_core_does_not_require_or_probe_java(self):
        args = self.cycle_args()
        with patch.object(runner.shutil, "which", side_effect=AssertionError("no Java probe")):
            self.assertEqual(runner.toolchain_snapshot(args), {})
        plan = runner.build_plan("core", args.interpreters)
        self.assertNotIn("pixel-jvm", [item["name"] for item in plan])

    @staticmethod
    def demo_result():
        return {"schema_version": 1, "status": "passed", "frames": 200,
            "evidence_kind": "authored_proposal_real_actor_and_kotlin_synthetic_feedback",
            "physical_execution": False, "physical_stop_confirmed": False,
            "language_model_inference": False, "policy_actor_inference": True, "hardware_access": False,
            "clock_kind": "deterministic_fixture_clock_not_realtime_measurement",
            "observation_size": 428, "actor_output_size": 12,
            "first_50_applied_actions_zero": True, "nonzero_actions_after_hold": True,
            "outcome_states": ["shadow_accepted", "expired"],
            "stop_outcome": "stop_requested", "revoked_frame_publication_rejected": True,
            "actor_parity": {"cases": 8, "max_abs_error": 5.96e-7},
            "asset_sha256": {"actor": "a" * 64}, "capability_digest": "b" * 64}

    def test_real_policy_demo_keeps_language_model_and_hardware_claims_separate(self):
        self.assertTrue(checks.validate_runtime_demo(self.demo_result()))
        for update in ({"language_model_inference": True}, {"policy_actor_inference": False},
                {"physical_execution": True}, {"physical_stop_confirmed": True},
                {"hardware_access": True}, {"observation_size": 180}, {"frames": 0},
                {"first_50_applied_actions_zero": False}, {"nonzero_actions_after_hold": False},
                {"outcome_states": ["shadow_accepted", "succeeded"]},
                {"stop_outcome": "succeeded"}, {"revoked_frame_publication_rejected": False}):
            with self.subTest(update=update):
                self.assertFalse(checks.validate_runtime_demo(dict(self.demo_result(), **update)))

    def test_runtime_demo_requires_finite_matching_reference_parity(self):
        for parity in ({"cases": 0, "max_abs_error": 0.0}, {"cases": 8, "max_abs_error": float("nan")},
                       {"cases": 8, "max_abs_error": .1}, {"cases": 8, "max_abs_error": True}):
            self.assertFalse(checks.validate_runtime_demo(dict(self.demo_result(), actor_parity=parity)))

    def test_firmware_host_is_explicit_and_never_clears_device_gate(self):
        args = self.cycle_args("firmware-host", ())
        plan = runner.build_plan(args.profile, args.interpreters)
        self.assertEqual([row["name"] for row in plan], ["firmware-host"])
        self.assertEqual(runner.build_plan("firmware", args.interpreters), [])
        with patch.object(runner, "source_snapshot", return_value=self.snapshot("same")), \
                patch.object(runner, "probe_python", return_value={"status": "PASS", "packages": {}}), \
                patch.object(runner, "execute_command", side_effect=AssertionError("must not run without headers")), \
                contextlib.redirect_stdout(io.StringIO()):
            report = runner.run_cycle(args, 1)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertIn("firmware", report["outstanding"])

    def test_external_firmware_headers_are_hashed_for_freshness(self):
        args = self.cycle_args("firmware-host", ())
        args.firmware_json_include = self.root / "headers"
        args.firmware_json_include.mkdir()
        header = args.firmware_json_include / "ArduinoJson.h"
        header.write_text("original")
        with patch.object(runner.shutil, "which", return_value=None):
            before = runner.firmware_snapshot(args)
            header.write_text("changed")
            after = runner.firmware_snapshot(args)
        self.assertNotEqual(before["headers_sha256"], after["headers_sha256"])
        args.profile = "offline"
        self.assertEqual(runner.firmware_snapshot(args), {})

    def test_firmware_header_directory_symlink_cannot_escape_fingerprint(self):
        args = self.cycle_args("firmware-host", ())
        args.firmware_json_include = self.root / "headers"
        directory = args.firmware_json_include / "ArduinoJson"
        directory.mkdir(parents=True)
        (args.firmware_json_include / "ArduinoJson.h").write_text('#include "ArduinoJson/detail.hpp"')
        (directory / "detail.hpp").write_text("unbound if the directory were followed")
        # Simulate a directory symlink without requiring symlink creation rights
        # on Windows; rglob still yields this directory just as with a real link.
        actual = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == directory or actual(path)), \
                patch.object(runner.shutil, "which", return_value=None):
            snapshot = runner.firmware_snapshot(args)
        self.assertNotIn("headers_sha256", snapshot)
        self.assertIn("unsupported", snapshot["headers_error"])

    def test_pytest_collection_without_execution_cannot_pass(self):
        evidence = checks.PytestEvidence()
        evidence.ids = ["a", "b", "c"]
        self.assertEqual(checks.pytest_status(0, evidence, 3), "FAIL")
        evidence.passed = ["a", "b"]
        self.assertEqual(checks.pytest_status(0, evidence, 3), "FAIL")
        evidence.passed = ["a", "b", "c", "c", "c"]
        self.assertEqual(checks.pytest_status(0, evidence, 3), "PASS")
        evidence.skipped = ["c"]
        self.assertEqual(checks.pytest_status(0, evidence, 3), "BLOCKED")
        evidence.failed = ["b"]
        self.assertEqual(checks.pytest_status(1, evidence, 3), "FAIL")

    def test_source_symlink_cannot_hide_external_source_changes(self):
        self.init_git()
        linked = self.root / "source.py"
        actual = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == linked or actual(path)):
            with self.assertRaisesRegex(ValueError, "source symlinks"):
                runner.source_snapshot(self.root)

    def test_timeout_terminates_child_that_started_its_own_session(self):
        if os.name != "posix":
            self.assertTrue(hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"))
            return
        pid_file = self.root / "detached.pid"
        child = f"""import json,os,subprocess,sys,time
from pathlib import Path
pid = os.getpid()
if sys.platform.startswith('linux'):
    identity = ['proc-starttime', Path(f'/proc/{{pid}}/stat').read_text().rsplit(')', 1)[1].split()[19]]
else:
    identity = ['ps-lstart', subprocess.check_output(['ps', '-p', str(pid), '-o', 'lstart='], text=True).strip()]
Path({str(pid_file)!r}).write_text(json.dumps({{'pid': pid, 'identity': identity}}))
time.sleep(60)
"""
        parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True); time.sleep(60)"
        result = runner.execute_command([sys.executable, "-c", parent], cwd=self.root,
            log=self.root / "detached.log", timeout=.5)
        self.assertEqual(result["status"], "TIMEOUT")
        self.assertTrue(pid_file.is_file())
        original = json.loads(pid_file.read_text())
        pid, identity = original["pid"], tuple(original["identity"])
        deadline, observations = time.monotonic() + 2, []
        terminated = False
        while time.monotonic() < deadline:
            if sys.platform.startswith("linux"):
                try:
                    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
                    current_identity, state = ("proc-starttime", fields[19]), fields[0]
                except FileNotFoundError:
                    terminated = True
                    break
            else:
                status = subprocess.run(["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat="],
                    capture_output=True, text=True, timeout=1)
                if status.returncode == 1 and not status.stdout.strip() and not status.stderr.strip():
                    terminated = True
                    break
                self.assertEqual(status.returncode, 0, status.stderr)
                started, state = status.stdout.strip().rsplit(None, 1)
                current_identity = ("ps-lstart", started)
            observations.append({"identity": current_identity, "state": state})
            # SIGKILL delivery is asynchronous. Require bounded observed death,
            # not an arbitrary immediate scheduler state. The child sleeps for 60 seconds,
            # so natural exit cannot satisfy this 2-second proof. Adopted zombies
            # may await PID 1 reaping and are already terminated. PID reuse likewise
            # proves the original child is gone, without signalling its successor.
            if current_identity != identity or state.startswith("Z"):
                terminated = True
                break
            time.sleep(.01)
        self.assertTrue(terminated, {"pid": pid, "identity": identity, "states": observations})

    def test_descendant_cleanup_does_not_signal_recycled_pid(self):
        original = {200: (100, ("proc-starttime", "123"))}
        recycled = {200: (1, ("proc-starttime", "456"))}
        calls = 0
        def snapshot():
            nonlocal calls
            calls += 1
            return original if calls == 1 else recycled
        with patch.object(runner, "process_table", side_effect=snapshot), patch.object(runner.os, "kill") as kill:
            runner.stop_owned_descendants(100)
        kill.assert_not_called()

    def test_bad_timeout_and_cycle_values_rejected(self):
        for args in (["--step-timeout", "nan"], ["--watch-timeout", "inf"], ["--max-cycles", "0"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                runner.main(args)


if __name__ == "__main__":
    unittest.main()
