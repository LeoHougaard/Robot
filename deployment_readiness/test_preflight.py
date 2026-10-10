"""Read-only preflight regressions; fixtures never open serial/network endpoints."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from deployment_readiness import preflight as p

ROOT = Path(__file__).resolve().parents[1]
BASE_FILES = [
    "AGENTS.md", "validation/runner.py", "validation/requirements.txt", "robot_llm/requirements-runtime.txt",
    "pixel_robot/tools/host_kotlin/dependencies.json", "pixel_robot/build.gradle.kts",
    "pixel_robot/gradle/wrapper/gradle-wrapper.properties", "pixel_robot/gradle/wrapper/gradle-wrapper.jar", "pixel_robot/gradlew",
    p.LEGACY + "policy_metadata.json", p.LEGACY + "policy_weights.npz",
    p.LEGACY + "assembly-1-12dof.calibration.json", p.LEGACY + "run_policy.py",
    p.ASSETS + "policy_metadata.json", p.ASSETS + "policy_android_manifest.json",
    p.ASSETS + "policy_actor.onnx", p.ASSETS + "policy_reference_vectors.json",
    p.ASSETS + "stride_reference.json", p.ASSETS + "assembly-four-leg-linkage-12dof.calibration.json",
    "robot_llm/data/capabilities.json", "robot_llm/contract.py", "robot_llm/supervisor.py",
    "control_center/profiles/assembly-1-12dof.json", "control_center/profiles/assembly-four-leg-linkage-12dof.json",
    "pixel_robot/app/build.gradle.kts", "robot_dog/firmware/robot-dog-control/platformio.ini",
    "robot_dog/firmware/robot-dog-control/src/main.cpp",
    "pixel_robot/app/src/main/java/com/leo/pixelrobot/robot/UsbRobotTransport.kt",
] + [p.POLICY + n + ".kt" for n in ("PolicyContract", "RobotCalibration", "OnnxPolicy", "StrideReference", "LoadedPolicyAssets")]
GOOD_PACKAGES = {**p.PINS, "torch": "2.5.1+cpu", "pyserial": "3.5"}
GOOD_TOOLS = {n: "/inventory-only/" + n for n in ("git", "bash", "java", "node", "adb", "pio", "pwsh")}


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        for name in BASE_FILES:
            dest = self.root / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)
        self.reader = p.Reader(self.root)

    def run_check(self, **kwargs):
        with patch.object(p, "kotlin_cache", return_value={"test_fixture": True}):
            return p.inspect(self.root, packages=GOOD_PACKAGES, tools=GOOD_TOOLS, **kwargs)

    def mutate(self, name, fn):
        file = self.root / name
        value = json.loads(file.read_bytes())
        fn(value)
        file.write_text(json.dumps(value))

    def row(self, report, name):
        return next(row for row in report["checks"] if row["id"] == name)

    def test_real_bundles_static_contracts_and_external_blocks(self):
        report = self.run_check()
        for name in ("legacy_bundle", "pixel_bundle", "pixel_shadow_capabilities", "synthetic_shadow_capabilities"):
            self.assertEqual(self.row(report, name)["status"], "PASS", name)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertFalse(report["deployment_ready"])
        for name in ("android", "firmware", "model", "isaac", "hardware"):
            self.assertEqual(self.row(report, name + "_execution_evidence")["status"], "BLOCKED")

    def test_offline_setup_scope_does_not_claim_execution_or_deployment(self):
        with patch.object(p.sys, "version_info", (3, 12, 10)):
            report = self.run_check(scope="offline-setup")
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["offline_validation_performed"])
        self.assertFalse(report["physical_safety_certified"])
        self.assertEqual(self.row(report, "hardware_execution_evidence")["status"], "BLOCKED")

    def test_missing_root_is_machine_readable_blocked(self):
        report = p.inspect(self.root / "absent", packages=GOOD_PACKAGES, tools=GOOD_TOOLS)
        self.assertEqual(report["status"], "BLOCKED")
        self.assertEqual(self.row(report, "repository")["data"]["reason_code"], "missing_file")

    def test_missing_dependency_is_blocked(self):
        packages = {**GOOD_PACKAGES, "baml-py": None}
        report = p.inspect(self.root, packages=packages, tools=GOOD_TOOLS)
        self.assertEqual(self.row(report, "offline_dependencies")["status"], "BLOCKED")

    def test_dependency_drift_and_non_cpu_local_tag_rejected(self):
        for name, value in (("pydantic", "2.12.0"), ("torch", "2.5.1+cu124"), ("pytest", "9.1.2")):
            with self.subTest(name=name):
                self.assertTrue(p.dependency_check({**GOOD_PACKAGES, name: value})["issues"])

    def test_missing_tools_are_blocked_without_execution(self):
        report = p.inspect(self.root, packages=GOOD_PACKAGES, tools={})
        self.assertEqual(self.row(report, "git")["status"], "BLOCKED")
        self.assertEqual(self.row(report, "bash")["status"], "BLOCKED")

    def test_untested_python_baseline_is_blocked(self):
        with patch.object(p.sys, "version_info", (3, 13, 0)):
            self.assertEqual(self.row(self.run_check(), "python")["status"], "BLOCKED")

    def test_missing_bundle_is_blocked(self):
        (self.root / (p.ASSETS + "policy_actor.onnx")).unlink()
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "BLOCKED")

    def test_corrupted_onnx_hash_fails(self):
        with (self.root / (p.ASSETS + "policy_actor.onnx")).open("ab") as out:
            out.write(b"drift")
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_corrupted_npz_hash_fails(self):
        (self.root / (p.LEGACY + "policy_weights.npz")).write_bytes(b"invalid")
        self.assertEqual(self.row(self.run_check(), "legacy_bundle")["status"], "FAIL")

    def test_unsupported_metadata_schema_fails(self):
        self.mutate(p.ASSETS + "policy_metadata.json", lambda m: m.update(schema_version=3))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_legacy_metadata_not_shadow_capability_schema(self):
        with self.assertRaises(p.Problem):
            p.capability_contract(self.reader, p.LEGACY + "policy_metadata.json")

    def test_runtime_source_drift_blocks_reviewed_contract(self):
        with (self.root / (p.POLICY + "PolicyContract.kt")).open("a") as out:
            out.write("\n// source changed\n")
        row = self.row(self.run_check(), "reviewed_contract_sources")
        self.assertEqual(row["status"], "BLOCKED")
        self.assertEqual(row["data"]["reason_code"], "contract_drift")

    def test_crlf_checkout_does_not_look_like_source_drift(self):
        before = self.row(self.run_check(), "reviewed_contract_sources")
        file = self.root / (p.POLICY + "PolicyContract.kt")
        file.write_bytes(file.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        after = self.row(self.run_check(), "reviewed_contract_sources")
        self.assertEqual(after, before)

    def test_candidate_identity_drift_fails(self):
        self.mutate(p.ASSETS + "policy_metadata.json", lambda m: m.update(checkpoint_sha256="0" * 64))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_selected_history_drift_fails(self):
        self.mutate(p.ASSETS + "policy_metadata.json", lambda m: m["history_selection"].update(indices=[0, 5, 10, 15, 20, 23]))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_action_contract_drift_fails(self):
        self.mutate(p.ASSETS + "policy_metadata.json", lambda m: m["action_contract"].update(low_pass_alpha=.8))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_command_envelope_drift_fails(self):
        self.mutate(p.ASSETS + "policy_metadata.json", lambda m: m["validated_command_limits"].update(forward_m_s=[-.4, .4]))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_reference_dimensions_fail(self):
        self.mutate(p.ASSETS + "policy_reference_vectors.json", lambda m: m["cases"][0]["observation"].pop())
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_stride_hash_drift_fails(self):
        self.mutate(p.ASSETS + "stride_reference.json", lambda m: m.update(lift_m=.09))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_calibration_flag_false_fails(self):
        self.mutate(p.ASSETS + "assembly-four-leg-linkage-12dof.calibration.json", lambda m: m.update(calibrated=False))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_calibration_flag_true_is_not_physical_evidence(self):
        result = p.pixel_bundle(self.reader)
        self.assertTrue(result["calibration"]["file_claims_calibrated"])
        self.assertFalse(result["calibration"]["physical_calibration_verified"])

    def test_calibration_semantic_mapping_fails(self):
        self.mutate(p.ASSETS + "assembly-four-leg-linkage-12dof.calibration.json", lambda m: m["joints"][0].update(servo_id=1))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_wrong_joint_coordinate_convention_fails(self):
        self.mutate(p.ASSETS + "assembly-four-leg-linkage-12dof.calibration.json", lambda m: m.update(joint_coordinate_convention="legacy_relative_knee"))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_missing_legacy_four_bar_linkage_fails(self):
        self.mutate(p.LEGACY + "assembly-1-12dof.calibration.json", lambda m: m["joints"][2].pop("linkage"))
        self.assertEqual(self.row(self.run_check(), "legacy_bundle")["status"], "FAIL")

    def test_wrong_pixel_imu_orientation_fails(self):
        self.mutate(p.ASSETS + "assembly-four-leg-linkage-12dof.calibration.json", lambda m: m["imu"].update(body_axis_from_sensor_axis=[[1, 0, 0], [0, 1, 0], [0, 0, 1]]))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_nonfinite_and_duplicate_json_rejected(self):
        for raw in (b'{"a": NaN}', b'{"a": 1e999}', b'{"a":1,"a":2}'):
            with self.subTest(raw=raw), self.assertRaises(p.Problem):
                p.strict_json(raw)

    def test_json_boolean_cannot_be_schema_number(self):
        self.mutate(p.ASSETS + "policy_metadata.json", lambda m: m.update(schema_version=True))
        self.assertEqual(self.row(self.run_check(), "pixel_shadow_capabilities")["status"], "FAIL")

    def test_malformed_nested_fields_fail_machine_readably(self):
        self.mutate(p.ASSETS + "policy_metadata.json", lambda m: m.update(current_observation_contract=None))
        self.assertEqual(self.row(self.run_check(), "pixel_bundle")["status"], "FAIL")

    def test_historical_profiles_warn_do_not_block_offline_setup(self):
        report = self.run_check()
        for profile in ("assembly-1-12dof", "assembly-four-leg-linkage-12dof"):
            self.assertEqual(self.row(report, "profile_snapshot_" + profile)["status"], "WARN")

    def test_legacy_baud_mismatch_is_visible(self):
        report = self.run_check()
        row = self.row(report, "legacy_transport_mismatch")
        self.assertEqual(row["status"], "WARN")
        self.assertEqual(row["data"]["legacy_usb_baud"], 921600)
        self.assertEqual(row["data"]["firmware_usb_baud"], 2000000)

    def test_pixel_firmware_baud_disagreement_fails(self):
        file = self.root / "pixel_robot/app/src/main/java/com/leo/pixelrobot/robot/UsbRobotTransport.kt"
        file.write_text(file.read_text().replace("2_000_000", "921_600"))
        self.assertEqual(self.row(self.run_check(), "source_toolchain_and_transport")["status"], "FAIL")

    def test_missing_android_sdk_is_blocked(self):
        report = self.run_check(android_sdk=self.root / "missing-sdk")
        self.assertEqual(self.row(report, "android_sdk_platform")["status"], "BLOCKED")

    def test_sdk_file_presence_never_clears_device_gate(self):
        sdk = self.root / "sdk"
        jar = sdk / "platforms/android-37/android.jar"
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b"presence-is-not-usability")
        report = self.run_check(android_sdk=sdk)
        self.assertEqual(self.row(report, "android_sdk_platform")["status"], "PASS")
        self.assertEqual(self.row(report, "android_execution_evidence")["status"], "BLOCKED")

    def test_unsafe_relative_path_rejected(self):
        for name in ("../secret", "/dev/ttyUSB0"):
            with self.subTest(name=name), self.assertRaises(p.Problem):
                self.reader.read(name)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_symlink_to_file_or_directory_rejected(self):
        (self.root / "linked").symlink_to(self.root / "AGENTS.md")
        (self.root / "linked-dir").symlink_to(self.root / "robot_dog", target_is_directory=True)
        for name in ("linked", "linked-dir/policy_runtime/policy_metadata.json"):
            with self.subTest(name=name), self.assertRaises(p.Problem):
                self.reader.read(name)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_fifo_rejected_before_open(self):
        os.mkfifo(self.root / "fifo")
        with patch.object(p.os, "open", side_effect=AssertionError("must not open FIFO")):
            with self.assertRaises(p.Problem):
                self.reader.read("fifo")

    def test_oversized_file_rejected(self):
        with self.assertRaises(p.Problem):
            self.reader.read("AGENTS.md", limit=2)

    def test_changed_file_between_reads_rejected(self):
        self.reader.read("AGENTS.md")
        (self.root / "AGENTS.md").write_text("changed")
        with self.assertRaises(p.Problem):
            self.reader.read("AGENTS.md")

    def test_npz_never_deserializes_object_payload(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            for i in range(10):
                header = repr({"descr": "|O", "shape": (1,), "fortran_order": False}).encode()
                archive.writestr(f"{i}.npy", b"\x93NUMPY\x01\x00" + len(header).to_bytes(2, "little") + header + b"not-a-pickle")
        with self.assertRaises(p.Problem):
            p.npz_headers(stream.getvalue())

    def test_no_project_imports_processes_sockets_or_file_mutation(self):
        def snapshot():
            return {str(f.relative_to(self.root)): (hashlib.sha256(f.read_bytes()).hexdigest(), f.stat().st_mtime_ns)
                    for f in self.root.rglob("*") if f.is_file()}
        before, env = snapshot(), dict(os.environ)
        imported = set(sys.modules)
        with ExitStack() as stack:
            for target in ("subprocess.Popen", "subprocess.run", "socket.socket", "socket.create_connection", "os.system"):
                stack.enter_context(patch(target, side_effect=AssertionError("forbidden side effect")))
            stack.enter_context(patch.object(Path, "write_text", side_effect=AssertionError("write forbidden")))
            stack.enter_context(patch.object(Path, "write_bytes", side_effect=AssertionError("write forbidden")))
            self.run_check()
        self.assertEqual(before, snapshot())
        self.assertEqual(env, dict(os.environ))
        self.assertFalse(any(n.startswith(("serial", "numpy", "torch", "robot_llm", "robot_dog", "validation")) for n in set(sys.modules) - imported))

    def test_audit_guard_rejects_mutating_and_external_events(self):
        for event, args in (("open", ("x", "wb", os.O_WRONLY)), ("open", ("x", None, os.O_CREAT)),
                            ("subprocess.Popen", ()), ("socket.connect", ()), ("socket.getaddrinfo", ()),
                            ("os.putenv", ()), ("os.remove", ()), ("os.mkdir", ()), ("os.system", ())):
            with self.subTest(event=event), self.assertRaises(PermissionError):
                p.audit_guard(event, args)
        p.audit_guard("open", ("x", "rb", os.O_RDONLY))

    def test_requirements_pins_come_from_authoritative_files(self):
        pins = p.canonical_pins(self.reader)["pins"]
        self.assertEqual(pins["numpy"], "2.2.6")
        self.assertEqual(pins["onnxruntime"], "1.24.3")
        self.assertEqual(pins["torch"], "2.5.1")

    def test_requirements_conflicts_fail_closed(self):
        file = self.root / "validation/requirements.txt"
        file.write_text(file.read_text() + "\npytest==0.0.0\n")
        with self.assertRaises(p.Problem):
            p.canonical_pins(self.reader)

    def test_requirements_cannot_escape_repository(self):
        (self.root / "validation/requirements.txt").write_text("-r ../../outside.txt\n")
        with self.assertRaises(p.Problem):
            p.canonical_pins(self.reader)

    def test_missing_kotlin_cache_is_blocked(self):
        with self.assertRaises(p.Problem) as caught:
            p.kotlin_cache(self.reader, self.root / "missing-cache")
        self.assertEqual(caught.exception.code, "missing_file")

    def test_kotlin_cache_hash_drift_fails(self):
        manifest = self.root / "pixel_robot/tools/host_kotlin/dependencies.json"
        manifest.write_text(json.dumps([{"file": "small.jar", "sha256": p.sha(b"right")}]))
        cache = self.root / "cache"
        cache.mkdir()
        (cache / "small.jar").write_bytes(b"wrong")
        with self.assertRaises(p.Problem):
            p.kotlin_cache(self.reader, cache)

    def test_transport_only_edit_does_not_invalidate_math_source_contract(self):
        file = self.root / (p.LEGACY + "run_policy.py")
        before = self.row(self.run_check(), "reviewed_contract_sources")
        file.write_text(file.read_text() + "\n# transport-only documentation change\n")
        self.assertEqual(self.row(self.run_check(), "reviewed_contract_sources"), before)

    def test_changed_runtime_contract_definition_blocks_review(self):
        file = self.root / (p.LEGACY + "run_policy.py")
        file.write_text(file.read_text().replace("POLICY_HISTORY = 4", "POLICY_HISTORY = 5"))
        self.assertEqual(self.row(self.run_check(), "reviewed_contract_sources")["status"], "BLOCKED")

    def add_node_gate(self, version="24.19.0"):
        file = self.root / "validation/runner.py"
        file.write_text(file.read_text() + f"\nNODE_VERSION = {version!r}\nNODE_STEP = Step('pixel-web-ui')\n")

    def test_node_requirement_read_from_canonical_declaration(self):
        self.add_node_gate()
        result = p.canonical_pins(self.reader)
        self.assertEqual(result["node_version"], "24.19.0")
        self.assertIn("pixel-web-ui", result["declared_steps"])

    def test_node_presence_never_claims_version_or_execution(self):
        self.add_node_gate()
        row = self.row(self.run_check(), "node")
        self.assertEqual(row["status"], "PASS")
        self.assertEqual(row["data"]["required_version"], "24.19.0")
        self.assertFalse(row["data"]["version_verified"])
        self.assertFalse(row["data"]["executed"])

    def test_missing_node_blocks_when_declared_gate_requires_it(self):
        self.add_node_gate()
        report = p.inspect(self.root, packages=GOOD_PACKAGES, tools={k: v for k, v in GOOD_TOOLS.items() if k != "node"})
        self.assertEqual(self.row(report, "node")["status"], "BLOCKED")

    def test_invalid_node_version_declaration_fails_closed(self):
        self.add_node_gate("latest")
        with self.assertRaises(p.Problem):
            p.canonical_pins(self.reader)

    def test_explicit_node_path_is_located_without_execution(self):
        self.add_node_gate()
        with patch.object(p.shutil, "which", return_value="/existing/node") as locate:
            report = self.run_check(node_executable="/selected/node")
        self.assertEqual(self.row(report, "node")["data"]["path"], "/existing/node")
        locate.assert_called_once_with("/selected/node")

    def test_sdk_presence_cannot_establish_license_acceptance(self):
        sdk = self.root / "sdk"
        jar = sdk / "platforms/android-37/android.jar"
        jar.parent.mkdir(parents=True)
        jar.write_bytes(b"platform-present")
        row = self.row(self.run_check(android_sdk=sdk), "android_sdk_license_review")
        self.assertEqual(row["status"], "BLOCKED")
        self.assertFalse(row["data"]["acceptance_verified"])
        self.assertFalse(row["data"]["accepted_by_preflight"])

    def test_bundled_calibration_does_not_verify_effective_app_snapshot(self):
        bundle = p.pixel_bundle(self.reader)
        self.assertFalse(bundle["effective_app_calibration_verified"])
        self.assertFalse(bundle["loaded_app_snapshot_verified"])
        self.assertEqual(bundle["calibration_source"], "repository bundled file only")

    def test_loaded_snapshot_source_drift_requires_review_only(self):
        file = self.root / (p.POLICY + "LoadedPolicyAssets.kt")
        file.write_text(file.read_text().replace("metadata.copyOf()", "metadata"))
        report = self.run_check(scope="offline-setup")
        row = self.row(report, "reviewed_contract_sources")
        self.assertEqual(row["status"], "BLOCKED")
        self.assertIn("LoadedPolicyAssets.kt", row["detail"])
        self.assertEqual(report["status"], "PASS")
        self.assertFalse(report["deployment_ready"])

    def evidence(self):
        directory = Path(self.temp.name) / "evidence"
        (directory / "cycle-001").mkdir(parents=True)
        files = [{"path": "AGENTS.md", "kind": "file", "size": (self.root / "AGENTS.md").stat().st_size,
                  "sha256": p.sha((self.root / "AGENTS.md").read_bytes())}]
        identity = {"head": "a" * 40, "base": "b" * 40, "files": files}
        manifest = {**identity, "sha256": p.digest(identity)}
        plan = [{"name": "test-check"}]
        report = {"schema_version": 1, "cycle": 1, "status": "PASS", "profile": "offline", "selection": [],
                  "partial_selection": False, "full_offline_passed": True, "all_requested_gates_passed": True,
                  "plan": plan, "plan_sha256": p.digest(plan),
                  "steps": [{"name": n, "required": True, "status": "PASS"} for n in ("test-check", "source-stability", "dependency-stability")],
                  "source_before": {"sha256": manifest["sha256"]}, "source_after": {"sha256": manifest["sha256"]}}
        loop = {"schema_version": 1, "status": "PASS", "cycles": [{"cycle": 1, "status": "PASS"}]}
        for name, value in (("latest.json", report), ("cycle-001/report.json", report), ("loop.json", loop), ("cycle-001/source-before.json", manifest)):
            (directory / name).write_text(json.dumps(value))
        return directory

    def test_report_consumption_never_claims_fresh_acceptance(self):
        result = p.consume_validation(self.evidence(), self.reader)
        self.assertTrue(result["recorded_full_offline_pass"])
        self.assertFalse(result["fresh_acceptance"])
        self.assertEqual(result["listed_source_files_changed"], [])

    def test_stale_report_source_detected(self):
        directory = self.evidence()
        (self.root / "AGENTS.md").write_text("changed")
        result = p.consume_validation(directory, self.reader)
        self.assertEqual(result["listed_source_files_changed"], ["AGENTS.md"])

    def test_partial_report_never_full_offline_pass(self):
        directory = self.evidence()
        for name in ("latest.json", "cycle-001/report.json"):
            value = json.loads((directory / name).read_text())
            value.update(profile="core", full_offline_passed=False)
            (directory / name).write_text(json.dumps(value))
        self.assertFalse(p.consume_validation(directory, self.reader)["recorded_full_offline_pass"])

    def test_loop_timeout_overrides_latest_pass_claim(self):
        directory = self.evidence()
        value = json.loads((directory / "loop.json").read_text())
        value["status"] = "TIMEOUT"
        (directory / "loop.json").write_text(json.dumps(value))
        self.assertFalse(p.consume_validation(directory, self.reader)["recorded_full_offline_pass"])

    def test_report_schema_drift_rejected(self):
        directory = self.evidence()
        (directory / "latest.json").write_text('{"schema_version": 2}')
        with self.assertRaises(p.Problem):
            p.consume_validation(directory, self.reader)

    def test_contradictory_pass_report_rejected(self):
        directory = self.evidence()
        for name in ("latest.json", "cycle-001/report.json"):
            value = json.loads((directory / name).read_text())
            value["steps"][0]["status"] = "FAIL"
            (directory / name).write_text(json.dumps(value))
        with self.assertRaises(p.Problem):
            p.consume_validation(directory, self.reader)

    def test_report_path_traversal_cannot_open_external_file(self):
        directory = self.evidence()
        manifest = json.loads((directory / "cycle-001/source-before.json").read_text())
        manifest["files"][0]["path"] = "../outside"
        identity = {k: manifest[k] for k in ("head", "base", "files")}
        manifest["sha256"] = p.digest(identity)
        (directory / "cycle-001/source-before.json").write_text(json.dumps(manifest))
        for name in ("latest.json", "cycle-001/report.json"):
            value = json.loads((directory / name).read_text())
            value["source_before"]["sha256"] = manifest["sha256"]
            value["source_after"]["sha256"] = manifest["sha256"]
            (directory / name).write_text(json.dumps(value))
        result = p.consume_validation(directory, self.reader)
        self.assertEqual(result["listed_source_files_changed"], ["../outside"])

    def test_cli_is_json_and_leaves_no_files_even_without_optional_dependencies(self):
        home = Path(self.temp.name) / "home"
        home.mkdir()
        before = {str(f): p.sha(f.read_bytes()) for f in self.root.rglob("*") if f.is_file()}
        result = subprocess.run([sys.executable, "-B", "-m", "deployment_readiness", "--root", str(self.root), "--format", "json"],
                                cwd=ROOT, env={"PATH": os.environ.get("PATH", ""), "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"},
                                text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "BLOCKED")
        self.assertEqual(before, {str(f): p.sha(f.read_bytes()) for f in self.root.rglob("*") if f.is_file()})
        self.assertEqual(list(home.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
