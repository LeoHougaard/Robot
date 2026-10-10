"""Isolated check process. Only the fixed checks below can be dispatched.

Python socket operations are denied, including DNS. This is a regression guard,
not a security sandbox for arbitrary native extensions or malicious source code.
"""
from __future__ import annotations

import argparse
import ast
from contextlib import redirect_stdout
import hashlib
import json
import math
import os
from pathlib import Path
import runpy
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def deny_network(event, args):
    if event in {"socket.connect", "socket.connect_ex", "socket.getaddrinfo",
                 "socket.gethostbyname", "socket.gethostbyaddr", "socket.sendto"}:
        raise RuntimeError("offline validation forbids network access")


PIXEL_CAPTURE_TEST = "test_fit_sim_from_run_data.FitSimFromRunDataTest.test_downloaded_walk_matches_calibration_golden"


def flatten_suite(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten_suite(test)
        else:
            yield test


def unit_suite(start: str, pattern: str, minimum: int, modules=(), required_modules=(), exclude_ids=()):
    directory = ROOT / start
    os.chdir(directory)
    sys.path.insert(0, str(directory))
    loader = unittest.TestLoader()
    suite = (loader.loadTestsFromNames(modules) if modules else
             loader.discover(str(directory), pattern=pattern))
    all_tests = list(flatten_suite(suite))
    discovered_ids = sorted(test.id() for test in all_tests)
    unknown_exclusions = sorted(set(exclude_ids) - set(discovered_ids))
    selected = [test for test in all_tests if test.id() not in exclude_ids]
    suite = unittest.TestSuite(selected)
    test_ids = sorted(test.id() for test in selected)
    missing_modules = [name for name in required_modules
                       if not any(item.startswith(name + ".") for item in test_ids)]
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    details = {"missing_test_modules": missing_modules, "collected": len(test_ids),
               "discovered": len(discovered_ids), "excluded_external_tests": list(exclude_ids),
               "unknown_exclusions": unknown_exclusions, "run": result.testsRun,
               "minimum": minimum, "skipped": len(result.skipped),
               "expected_failures": len(result.expectedFailures),
               "failures": len(result.failures), "errors": len(result.errors),
               "unexpected_successes": len(result.unexpectedSuccesses),
               "test_ids_sha256": hashlib.sha256("\n".join(test_ids).encode()).hexdigest()}
    if not result.wasSuccessful() or result.testsRun < minimum or missing_modules or unknown_exclusions:
        return "FAIL", details
    if result.skipped or result.expectedFailures:
        return "BLOCKED", details
    return "PASS", details


CPU_FILES = (
    "test_delivery_timing.py", "test_delivery_gait_highlift.py",
    "test_deployable_dynamics.py", "test_delivery_critic.py", "test_delivery_height.py",
    "test_delivery_reward.py", "test_delivery_evaluation.py", "test_delivery_provenance.py",
)


class PytestEvidence:
    def __init__(self):
        self.ids, self.failed, self.skipped, self.passed = [], [], [], []
    def pytest_collection_finish(self, session):
        self.ids = sorted(item.nodeid for item in session.items)
    def pytest_runtest_logreport(self, report):
        if report.failed:
            self.failed.append(report.nodeid)
        if report.skipped or hasattr(report, "wasxfail"):
            self.skipped.append(report.nodeid)
        if report.when == "call" and report.passed:
            self.passed.append(report.nodeid)


def pytest_status(code, evidence, minimum):
    if int(code) or evidence.failed or len(evidence.ids) < minimum:
        return "FAIL"
    if evidence.skipped:
        return "BLOCKED"
    # Collection is not execution (e.g. config addopts=--collect-only). Native
    # subtests may add repeated call reports; every collected parent must pass.
    return "PASS" if set(evidence.ids).issubset(set(evidence.passed)) else "FAIL"


def cpu_suite():
    import pytest
    import torch
    os.chdir(ROOT / "training")
    sys.path.insert(0, str(ROOT / "training"))
    collector = PytestEvidence()
    code = pytest.main(["-q", "-o", "addopts=", "-p", "no:cacheprovider", *CPU_FILES], plugins=[collector])
    details = {"collected": len(collector.ids), "minimum": 68,
               "passed_reports": len(collector.passed), "failed_reports": len(collector.failed),
               "executed_unique": len(set(collector.ids) & set(collector.passed)),
               "missing_execution": sorted(set(collector.ids) - set(collector.passed)),
               "skipped_or_xfail_reports": len(collector.skipped),
               "torch_version": torch.__version__, "cuda_disabled": os.environ.get("CUDA_VISIBLE_DEVICES") == "",
               "test_ids_sha256": hashlib.sha256("\n".join(collector.ids).encode()).hexdigest()}
    status = pytest_status(code, collector, 68)
    return status, details


# Intentional bad fixture responses must remain visible; zero error counts would
# be a regression in the evaluator, not evidence that the model got safer.
FIXTURE_EXPECTED = {
    "cases": 32, "semantic_correct_count": 25, "invalid_response_count": 3,
    "timed_out_count": 1, "wrong_accept_count": 2, "unsafe_accept_count": 1,
    "repaired_invalid_candidates": 1, "provider_error_count": 1,
}


def fixture_check(artifact: Path):
    from robot_llm.evaluate import main
    if main(["--mode", "fixtures", "--output", str(artifact)]) != 0:
        return "FAIL", {"reason": "fixture evaluator failed"}
    report = json.loads(artifact.read_text(encoding="utf-8"))
    summary = report["providers"]["fixtures"]["summary"]
    wrong = [key for key, value in FIXTURE_EXPECTED.items() if summary.get(key) != value]
    rows = report["providers"]["fixtures"]["cases"]
    if report.get("physical_execution") is not False or report.get("shadow_only") is not True:
        wrong.append("shadow-only boundary")
    if not all(row.get("synthetic") is True for row in rows) or len(rows) != 32:
        wrong.append("synthetic fixture identity")
    return ("FAIL" if wrong else "PASS"), {"summary": summary, "mismatches": wrong,
        "scope": "Authored fixture regression only; wrong/unsafe admits are deliberate test doubles"}


def gait_check(artifact: Path):
    sys.path.insert(0, str(ROOT / "training"))
    from probe_delivery_gait import main
    with artifact.open("w", encoding="utf-8") as stream, redirect_stdout(stream):
        main()
    report = json.loads(artifact.read_text(encoding="utf-8"))
    if set(report) != {"standard", "highlift"}:
        return "FAIL", {"reason": "gait variants are missing"}
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("nonfinite gait diagnostic")
        if isinstance(value, dict):
            for item in value.values(): finite(item)
        elif isinstance(value, list):
            for item in value: finite(item)
    finite(report)
    if any(row.get("command_count") != 14 or row.get("phase_samples_per_command") != 201
           or len(row.get("commands", [])) != 14 for row in report.values()):
        return "FAIL", {"reason": "gait diagnostic coverage changed"}
    return "PASS", {"scope": "Diagnostic generated and validated; NOT gait acceptance or physical safety",
        "variants": {name: {key: row[key] for key in ("command_count", "phase_samples_per_command",
            "raw_peak_joint_target_rad", "min_bilateral_residual_headroom_rad",
            "full_residual_envelope_fits_fraction")} for name, row in report.items()}}


def syntax_check(manifest: Path, bash=False):
    paths = json.loads(manifest.read_text(encoding="utf-8"))["files"]
    files = [ROOT / row["path"] for row in paths if row.get("kind") != "deleted"
             and row["path"].endswith(".sh" if bash else ".py")]
    if not files:
        return "BLOCKED", {"reason": "no source files found"}
    executable = shutil.which("bash") if bash else None
    if bash and executable is None:
        return "BLOCKED", {"reason": "Bash unavailable; shell syntax not tested"}
    for path in files:
        if bash:
            result = subprocess.run([executable, "-n", str(path)], check=False)
            if result.returncode:
                return "FAIL", {"reason": "Bash syntax failure", "file": str(path.relative_to(ROOT))}
        else:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    return "PASS", {"files_checked": len(files)}


def validate_host_report(report, *, returncode, scope, minimum, required_classes=()):
    """A successful host process is not Android/device/firmware acceptance."""
    statuses = {"passed": "PASS", "failed": "FAIL", "blocked": "BLOCKED"}
    if (type(report) is not dict or type(report.get("schema_version")) is not int
            or report["schema_version"] != 1 or report.get("scope") != scope
            or report.get("status") not in statuses
            or report.get("hardware_access") is not False
            or report.get("android_build_verified") is not False):
        return "FAIL", {"reason": "invalid host result or unsupported physical/build claim"}
    status = statuses[report["status"]]
    if returncode != {"PASS": 0, "FAIL": 1, "BLOCKED": 2}[status]:
        return "FAIL", {"reason": "host exit/result disagree"}
    ids = report.get("test_ids")
    classes = report.get("test_classes")
    if status == "PASS" and (type(report.get("test_count")) is not int
            or report["test_count"] < minimum or type(report.get("ignored")) is not int or report["ignored"] != 0
            or type(report.get("assumption_failures")) is not int or report["assumption_failures"] != 0
            or report.get("failures") != [] or not report.get("compiled_production_sources")
            or not isinstance(report.get("source_fingerprint"), str)
            or len(report["source_fingerprint"]) != 64
            or not isinstance(report.get("java_version"), str) or not report["java_version"].strip()
            or type(ids) is not list or any(type(item) is not str or not item for item in ids)
            or len(ids) != report["test_count"] or len(set(ids)) != len(ids)
            or report.get("finished_test_ids") != ids
            or type(classes) is not list or any(type(item) is not str or not item for item in classes)
            or not set(required_classes).issubset(classes)
            or any(not any(item.startswith(name + ".") for item in ids) for name in required_classes)):
        return "FAIL", {"reason": "host coverage incomplete or ignored/failing tests"}
    return status, {"scope": scope, "test_count": report.get("test_count", 0),
        "minimum": minimum, "ignored": report.get("ignored"),
        "assumption_failures": report.get("assumption_failures"), "hardware_access": False,
        "android_build_verified": False, "source_fingerprint": report.get("source_fingerprint"),
        "test_ids_sha256": hashlib.sha256("\n".join(ids or []).encode()).hexdigest(),
        "required_test_classes": list(required_classes), "test_classes": classes,
        "java_version": report.get("java_version"),
        "compile_only_android_boundaries": report.get("compile_only_android_boundaries", []),
        "compile_only_production_sources": report.get("compile_only_production_sources", []),
        "excluded_sources": report.get("excluded_sources", []),
        "reason": report.get("error") or "Host software checks only; device/build acceptance remains outstanding"}


def validate_runtime_demo(report):
    """Keep authored feedback, local actor compute and physical claims separate."""
    expected = {
        "schema_version": 1, "status": "passed", "frames": 200,
        "evidence_kind": "authored_proposal_real_actor_and_kotlin_synthetic_feedback",
        "physical_execution": False, "physical_stop_confirmed": False,
        "language_model_inference": False, "policy_actor_inference": True,
        "hardware_access": False,
        "clock_kind": "deterministic_fixture_clock_not_realtime_measurement",
        "observation_size": 428, "actor_output_size": 12,
        "first_50_applied_actions_zero": True, "nonzero_actions_after_hold": True,
        "outcome_states": ["shadow_accepted", "expired"],
        "stop_outcome": "stop_requested", "revoked_frame_publication_rejected": True,
    }
    if type(report) is not dict:
        return False
    for key, value in expected.items():
        if type(report.get(key)) is not type(value) or report[key] != value:
            return False
    parity = report.get("actor_parity")
    if type(parity) is not dict or parity.get("cases") != 8:
        return False
    error = parity.get("max_abs_error")
    if type(error) not in (int, float) or not math.isfinite(error) or not 0 <= error < 2e-6:
        return False
    if not report.get("asset_sha256") or not report.get("capability_digest"):
        return False
    return True


def pixel_jvm_check(artifact: Path, extra=()):
    parser = argparse.ArgumentParser()
    parser.add_argument("--jvm-cache", type=Path)
    options = parser.parse_args(extra)
    namespace = runpy.run_path(str(ROOT / "pixel_robot/tools/run_host_kotlin_tests.py"))
    arguments = ["--rebuild", "--output", str(artifact)]
    if options.jvm_cache is not None:
        arguments.extend(["--cache-dir", str(options.jvm_cache)])
    code = namespace["main"](arguments)
    report = json.loads(artifact.read_text(encoding="utf-8"))
    status, details = validate_host_report(report, returncode=code,
        scope="pixel-production-kotlin-host-unit-tests", minimum=157,
        required_classes=("com.leo.pixelrobot.policy.ShadowIntentSessionTest",
            "com.leo.pixelrobot.policy.LoadedPolicyAssetsTest",
            "com.leo.pixelrobot.policy.ShadowRequestCoordinatorTest",
            "com.leo.pixelrobot.shadow.ShadowProviderTest",
            "com.leo.pixelrobot.shadow.ShadowHttpApiTest",
            "com.leo.pixelrobot.robot.RobotControlServerTest",
            "com.leo.pixelrobot.policy.PhysicalCommandAuthorityTest",
            "com.leo.pixelrobot.robot.BoundedSerialWriterTest"))
    if status == "PASS":
        # The harness checks every pinned jar before compiling. Check again so
        # concurrent dependency/cache edits cannot yield accepted evidence.
        cache = options.jvm_cache or namespace["PIXEL"] / ".tooling/host-kotlin"
        try:
            jars = namespace["dependencies"](cache.resolve(), False)
        except (OSError, RuntimeError):
            return "BLOCKED", {"reason": "pinned JVM dependency cache changed during the check"}
        details["dependency_hashes"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in jars}
    return status, details


def shadow_script_digests(root=ROOT):
    """Hash the actual panel IIFE selected by the deterministic Node harness."""
    html_path = Path(root) / "pixel_robot/app/src/main/assets/robot_control.html"
    test_path = Path(root) / "pixel_robot/tools/test_shadow_ui.cjs"
    html_bytes = html_path.read_bytes()
    # Match Node's UTF-8 source slice exactly, preserving CRLF bytes.
    html = html_bytes.decode("utf-8")
    marker = "// This panel never calls"
    if html.count(marker) != 1:
        raise ValueError("shadow panel source marker missing or ambiguous")
    start = html.index(marker)
    end = html.find("</script>", start)
    if end < 0: raise ValueError("shadow panel closing script tag absent")
    return {"production_html_sha256": hashlib.sha256(html_bytes).hexdigest(),
        "production_script_sha256": hashlib.sha256(html[start:end].encode("utf-8")).hexdigest(),
        "test_source_sha256": hashlib.sha256(test_path.read_bytes()).hexdigest()}


def validate_web_ui_report(report, *, returncode, expected_digests, minimum):
    from validation.runner import NODE_VERSION
    expected = {"schema_version": 1, "scope": "pixel-production-shadow-panel-dom-stub-tests",
        "node_version": "v" + NODE_VERSION, "network_access": False, "dom_environment": "stub",
        "android_webview_verified": False, "hardware_access": False,
        "live_control_script_executed": False,
        "production_html_path": "pixel_robot/app/src/main/assets/robot_control.html", **expected_digests}
    if type(report) is not dict or any(type(report.get(key)) is not type(value) or report[key] != value
            for key, value in expected.items()):
        return "FAIL", {"reason": "invalid UI report, source identity or unsupported runtime claim"}
    if report.get("status") != "passed" or returncode != 0:
        return "FAIL", {"reason": "production shadow-panel tests did not pass"}
    ids = report.get("test_ids")
    if (type(report.get("test_count")) is not int or report["test_count"] < minimum
            or type(ids) is not list or any(type(item) is not str or not item for item in ids)
            or len(ids) != report["test_count"] or len(set(ids)) != len(ids)
            or report.get("passed_test_ids") != ids or report.get("failures") != []
            or any(type(report.get(key)) is not int or report[key] != 0 for key in ("skipped", "cancelled", "todo"))):
        return "FAIL", {"reason": "UI coverage incomplete, skipped, cancelled, todo or failing"}
    return "PASS", {**expected, "test_count": len(ids), "minimum": minimum,
        "test_ids_sha256": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
        "scope_note": "Actual shadow-panel JavaScript with authored DOM/fetch stubs; no browser/WebView, live provider or physical acceptance"}


def pixel_web_ui_check(artifact: Path, extra=()):
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True)
    options = parser.parse_args(extra)
    script = ROOT / "pixel_robot/tools/test_shadow_ui.cjs"
    before = shadow_script_digests()
    code = subprocess.run([options.node, str(script), "--output", str(artifact)],
        cwd=ROOT, check=False).returncode
    if before != shadow_script_digests():
        return "BLOCKED", {"reason": "production panel or test script changed during execution"}
    if not artifact.is_file() or artifact.stat().st_size > 4 * 1024 * 1024:
        return "FAIL", {"reason": "missing or oversized UI result"}
    report = json.loads(artifact.read_text(encoding="utf-8"))
    return validate_web_ui_report(report, returncode=code, expected_digests=before, minimum=48)


def firmware_host_check(artifact: Path, extra=()):
    parser = argparse.ArgumentParser()
    parser.add_argument("--firmware-json-include", required=True, type=Path)
    parser.add_argument("--firmware-compiler", default="g++")
    options = parser.parse_args(extra)
    compiler = shutil.which(options.firmware_compiler)
    include = options.firmware_json_include
    version_file = include / "ArduinoJson/version.hpp"
    if compiler is None or not (include / "ArduinoJson.h").is_file() or not version_file.is_file():
        return "BLOCKED", {"reason": "host compiler or ArduinoJson headers unavailable"}
    match = re.search(r'#define ARDUINOJSON_VERSION "([^"]+)"', version_file.read_text())
    if match is None or match.group(1) not in {"7.0.4", "7.4.3"}:
        return "BLOCKED", {"reason": "requires tested ArduinoJson 7.0.4 or 7.4.3 headers"}
    version = subprocess.run([compiler, "--version"], capture_output=True, text=True, timeout=15, check=True)
    script = ROOT / "robot_dog/firmware/robot-dog-control/test/test_policy_commands.py"
    sys.argv = [str(script), "--json-include", str(include), "--compiler", compiler]
    runpy.run_path(str(script), run_name="__main__")
    report = {"schema_version": 1, "status": "passed", "scope": "native-firmware-fake-bus-assertions",
        "hardware_access": False, "board_build_verified": False, "physical_execution": False,
        "arduinojson_version": match.group(1), "compiler_version": version.stdout.strip(),
        "source": str(script.relative_to(ROOT)), "source_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
        "covered": ["production policy handlers", "UART framing", "watchdog transitions", "read-only monitor"],
        "limitation": "Extracted production handlers and fake dependencies; no ESP32 binary execution, serial port, or board timing verification"}
    artifact.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return "PASS", report


def dispatch(name, artifact, manifest, extra):
    suites = {
        "llm-unit": ("robot_llm/tests", "test_*.py", 74, (),
            ("test_contract", "test_evaluate", "test_providers", "test_supervisor",
             "test_session", "test_qualify", "test_qualification_corpus", "test_runtime_bridge",
             "test_runtime_process")),
        "qwen-unit": ("autoresearch", "test_*.py", 41, ()),
        "control-unit": ("control_center", "test_*.py", 25, ()),
        "policy-unit": ("training", "test_*.py", 23,
                        ("test_evaluate_simple_dog_policy", "test_export_v2_policy")),
        "onshape-unit": ("onshape_mate_mirror", "test_*.py", 11,
                         ("test_onshape_mate_mirror",)),
        "pixel-tools-unit": ("pixel_robot/tools", "test_*.py", 16, (),
            ("test_analyze_training_capture", "test_fit_sim_from_run_data", "test_inspect_run_data",
             "test_plot_run_data", "test_run_data_source", "test_trim_training_capture"), (PIXEL_CAPTURE_TEST,)),
        "pixel-capture": ("pixel_robot/tools", "", 1, (PIXEL_CAPTURE_TEST,)),
        "preflight-unit": (".", "", 65, ("deployment_readiness.test_preflight",)),
        "policy-runtime-unit": ("robot_dog/policy_runtime", "test_*.py", 87, (),
            ("test_policy_runtime", "test_goal_controller", "test_offline_transport")),
        "baml-unit": (".", "", 8, ("robot_llm.tests.baml_checks",)),
        "runner-unit": ("validation", "test_*.py", 61, (), ("test_runner",)),
    }
    if name in suites:
        status, details = unit_suite(*suites[name])
        scopes = {
            "policy-runtime-unit": "Legacy 180-value Python runtime with fake transport; no serial port or hardware",
            "pixel-tools-unit": "Portable authored capture/data-tool fixtures; original recorded walk is a separate gate",
            "pixel-capture": "Regression against one retained capture; does not authenticate origin or certify robot safety",
            "preflight-unit": "Read-only local setup diagnostic regressions; not backend/device acceptance",
        }
        if name in scopes: details["scope"] = scopes[name]
        return status, details
    if name == "pixel-runtime":
        parser = argparse.ArgumentParser()
        parser.add_argument("--jvm-cache", type=Path)
        options = parser.parse_args(extra)
        if options.jvm_cache is not None:
            os.environ["ROBOT_PIXEL_JVM_CACHE"] = str(options.jvm_cache)
        status, details = unit_suite(".", "", 20, ("robot_llm.tests.pixel_runtime_checks",))
        details["scope"] = "Actual current Pixel actor and Kotlin math with authored/synthetic feedback; no language-model inference or hardware"
        if status == "PASS":
            from robot_llm.runtime_demo import run_demo
            demo = run_demo(allow_shadow=True, allow_unqualified_metadata=True, frames=200)
            artifact.write_text(json.dumps(demo, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
            if not validate_runtime_demo(demo):
                return "FAIL", {**details, "reason": "runtime demo scope, parity or lifecycle invariant failed"}
            details["demo"] = {key: demo[key] for key in ("frames", "actor_parity", "observation_size",
                "actor_output_size", "outcome_states", "language_model_inference", "policy_actor_inference",
                "hardware_access", "clock_kind", "stop_outcome", "revoked_frame_publication_rejected",
                "cold_start_wall_seconds", "fixture_run_wall_seconds")}
        return status, details
    if name == "firmware-host": return firmware_host_check(artifact, extra)
    if name == "pixel-jvm": return pixel_jvm_check(artifact, extra)
    if name == "pixel-web-ui": return pixel_web_ui_check(artifact, extra)
    if name == "cpu-torch": return cpu_suite()
    if name == "fixtures": return fixture_check(artifact)
    if name == "gait-probe": return gait_check(artifact)
    if name == "python-syntax": return syntax_check(manifest)
    if name == "bash-syntax": return syntax_check(manifest, bash=True)
    if name == "baml-generate":
        from baml_py import invoke_runtime_cli
        sys.argv = ["baml-cli", "generate", "--from", str(ROOT / "robot_llm/baml_src")]
        try:
            result = invoke_runtime_cli()
        except SystemExit as exc:
            result = exc.code
        return ("PASS" if result in (None, 0) else "FAIL"), {"tool": "baml-cli generate", "result": result}
    if name == "live-model":
        live = argparse.ArgumentParser()
        live.add_argument("--model-report", type=Path, required=True)
        live.add_argument("--corpus", type=Path, required=True)
        live.add_argument("--capabilities", type=Path, required=True)
        live.add_argument("--criteria", type=Path, required=True)
        live.add_argument("--corpus-role", choices=("development", "holdout"), required=True)
        config = live.parse_args(extra)
        from robot_llm.qualify import load_criteria, qualify_file
        result = qualify_file(config.model_report, corpus_path=config.corpus,
            capabilities_path=config.capabilities, criteria=load_criteria(config.criteria),
            corpus_role=config.corpus_role)
        artifact.write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
        status = result["status"].upper()
        if status not in {"PASS", "FAIL", "BLOCKED"} or result.get("software_accepted") is not (status == "PASS"):
            return "FAIL", {"reason": "contradictory qualification result"}
        return status, {"software_accepted": result["software_accepted"], "issues": result["issues"],
            "scope": "Read-only qualification against current source; no model inference or physical certification"}
    if name == "offline-guard-probe":
        import socket
        try:
            socket.getaddrinfo("this-must-never-be-resolved.invalid", 443)
        except RuntimeError:
            return "PASS", {"denied_before_dns": True}
        return "FAIL", {"denied_before_dns": False}
    raise ValueError("unknown fixed validation check")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("name")
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args, extra = parser.parse_known_args(argv)
    sys.addaudithook(deny_network)
    try:
        status, details = dispatch(args.name, args.artifact, args.manifest, extra)
    except (ImportError, ModuleNotFoundError) as exc:
        status, details = "BLOCKED", {"reason": "missing dependency", "exception": type(exc).__name__}
    except Exception as exc:
        import traceback
        traceback.print_exc()
        status, details = "FAIL", {"reason": "check raised an exception", "exception": type(exc).__name__}
    args.result.write_text(json.dumps({"schema_version": 1, "check": args.name,
        "status": status, "details": details}, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")
    return 0 if status == "PASS" else (2 if status == "BLOCKED" else 1)


if __name__ == "__main__":
    raise SystemExit(main())
