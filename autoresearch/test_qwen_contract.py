"""Offline Qwen contract, evidence, and duration regressions. No live inference."""

from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
import robot_autoresearch as subject


def decision(**overrides: object) -> dict:
    return {
        "action": "continue",
        "confidence": 0.8,
        "summary": "The measured tracking error improved.",
        "visual_findings": ["No fall in the supplied sampled frames."],
        "tuning_changes": {},
        **overrides,
    }


def completion(content: object, **message_overrides: object) -> dict:
    return {
        "id": "mock-completion",
        "model": "mock-model",
        "choices": [{
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": content, **message_overrides},
        }],
    }


class QwenContractTests(unittest.TestCase):
    def test_accepts_all_actions_without_coercing_numbers(self) -> None:
        for action in ("continue", "hold", "escalate"):
            expected = decision(action=action)
            if action == "escalate":
                expected["codex_request"] = "Review the repeated lateral drift."
            with self.subTest(action=action):
                self.assertEqual(
                    subject.parse_qwen_decision(" \n" + json.dumps(expected) + "\t"),
                    expected,
                )

    def test_rejects_wrappers_and_nonobject_documents(self) -> None:
        good = json.dumps(decision())
        for raw in ("", "null", "[]", "true", "1", json.dumps(good),
                    "analysis\n" + good, "```json\n" + good + "\n```",
                    good + " commentary", good + good, good[:-1], "\ufeff" + good):
            with self.subTest(raw=raw):
                with self.assertRaises(subject.SupervisorError):
                    subject.parse_qwen_decision(raw)

    def test_rejects_duplicate_keys_at_every_depth(self) -> None:
        good = json.dumps(decision())
        for raw in (
            good.replace('"action": "continue"', '"action":"hold","action":"continue"'),
            good.replace('"tuning_changes": {}',
                         '"tuning_changes":{"gait_reward_scale":5.1,"gait_reward_scale":5.2}'),
            good.replace('"action": "continue"',
                         '"action":"hold","\\u0061ction":"continue"'),
        ):
            with self.subTest(raw=raw):
                with self.assertRaisesRegex(subject.SupervisorError, "duplicate"):
                    subject.parse_qwen_decision(raw)

    def test_rejects_nonfinite_and_overflowing_numbers(self) -> None:
        for raw_number in ("NaN", "Infinity", "-Infinity", "1e999", "-1e999", "9" * 400):
            with self.subTest(number=raw_number):
                raw = json.dumps(decision()).replace('"confidence": 0.8',
                                                       '"confidence":' + raw_number)
                with self.assertRaises(subject.SupervisorError):
                    subject.parse_qwen_decision(raw)
                raw = json.dumps(decision()).replace('"tuning_changes": {}',
                    '"tuning_changes":{"gait_reward_scale":' + raw_number + '}')
                with self.assertRaises(subject.SupervisorError):
                    subject.parse_qwen_decision(raw)

    def test_rejects_missing_unknown_or_mistyped_fields(self) -> None:
        cases = [decision(**{field: value}) for field, values in {
            "action": ["stop", "Continue", 1, None, []],
            "confidence": [-0.1, 1.1, True, "0.8", None, {}],
            "summary": [None, [], "", "  ", "\ud800"],
            "visual_findings": [None, "none", [1], ["  "], [{}]],
            "tuning_changes": [None, [], "{}", {"gait_reward_scale": True},
                               {"gait_reward_scale": "5.5"}, {"shell": 1.0}],
            "codex_request": [True, None, {}, "not an escalation"],
        }.items() for value in values]
        cases.append(decision(command="docker stop"))
        for field in subject.qwen_decision_schema()["required"]:
            item = decision()
            del item[field]
            cases.append(item)
        for item in cases:
            with self.subTest(item=item):
                with self.assertRaises(subject.SupervisorError):
                    subject.parse_qwen_decision(json.dumps(item))

    def test_escalation_requires_nonblank_request(self) -> None:
        for codex in (None, "", "  "):
            item = decision(action="escalate")
            if codex is not None:
                item["codex_request"] = codex
            with self.subTest(codex=codex):
                with self.assertRaises(subject.SupervisorError):
                    subject.parse_qwen_decision(json.dumps(item))

    def test_exactly_two_allowlisted_proposals_leave_bounds_to_existing_gate(self) -> None:
        proposed = {"body_vel_reward_scale": 5.5, "gait_reward_scale": 8.0}
        parsed = subject.parse_qwen_decision(json.dumps(decision(tuning_changes=proposed)))
        applied, notes = subject.bounded_tuning({}, parsed["tuning_changes"])
        self.assertEqual(applied, {"body_vel_reward_scale": 5.5})
        self.assertTrue(any("25%" in note for note in notes))
        proposed["foot_slip_penalty_scale"] = -2.2
        with self.assertRaisesRegex(subject.SupervisorError, "too many"):
            subject.parse_qwen_decision(json.dumps(decision(tuning_changes=proposed)))

    def test_content_size_and_parser_depth_are_bounded_failures(self) -> None:
        with self.assertRaisesRegex(subject.SupervisorError, "64 KiB"):
            subject.parse_qwen_decision(json.dumps(decision(summary="x" * 65536)))
        with self.assertRaises(subject.SupervisorError):
            subject.parse_qwen_decision("[" * 2000 + "0" + "]" * 2000)
        with self.assertRaises(subject.SupervisorError):
            subject.parse_qwen_decision("\ud800")

    def test_schema_modes_are_explicit_and_generated_from_same_contract(self) -> None:
        self.assertEqual(subject.qwen_response_format("json_object"), {"type": "json_object"})
        native = subject.qwen_response_format("json_schema")
        self.assertEqual(native["type"], "json_schema")
        self.assertEqual(native["json_schema"]["schema"], subject.qwen_decision_schema())
        self.assertEqual(set(native["json_schema"]["schema"]["properties"]
                             ["tuning_changes"]["properties"]), set(subject.RANGES))
        with self.assertRaises(subject.SupervisorError):
            subject.qwen_response_format("auto")

    def test_schema_extension_cannot_silently_bypass_local_validation(self) -> None:
        schema = subject.qwen_decision_schema()
        schema["properties"]["summary"]["pattern"] = "unsupported-local-keyword"
        with patch.object(subject, "qwen_decision_schema", return_value=schema):
            with self.assertRaisesRegex(subject.SupervisorError, "schema vocabulary"):
                subject.parse_qwen_decision(json.dumps(decision()))

    def test_manifest_mode_defaults_and_rejects_unknown_modes(self) -> None:
        source = json.loads((_HERE / "robots" / "simple-dog.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            manifest = Path(directory) / "manifest.json"
            manifest.write_text(json.dumps(source))
            self.assertEqual(subject.load_manifest(manifest)["qwen_response_format"], "json_object")
            for mode in ("json_schema", "json_object"):
                source["qwen_response_format"] = mode
                manifest.write_text(json.dumps(source))
                self.assertEqual(subject.load_manifest(manifest)["qwen_response_format"], mode)
            for mode in ("auto", True, None, {}):
                source["qwen_response_format"] = mode
                manifest.write_text(json.dumps(source))
                with self.assertRaises(subject.SupervisorError):
                    subject.load_manifest(manifest)


class QwenProviderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.supervisor = object.__new__(subject.Supervisor)
        self.supervisor.manifest = {"qwen_model": "mock-model"}
        self.supervisor.run_dir = self.root
        self.supervisor.run_id = "mock-run"
        self.supervisor.state = {"cycle": 3, "tuning": {}, "checkpoint": "source.pth"}
        self.addCleanup(patch.stopall)
        patch.object(subject, "AUTORESEARCH_ROOT", _HERE).start()
        patch.object(subject, "run", side_effect=AssertionError("Unexpected external process")).start()

    def ask(self, body: object, video: Path | None = None) -> tuple:
        encoded = body if isinstance(body, bytes) else json.dumps(body).encode()
        with patch.object(subject.urllib.request, "urlopen", return_value=io.BytesIO(encoded)) as provider:
            result = self.supervisor.ask_qwen({"tracking_error": 0.1}, video)
        self.request = provider.call_args.args[0]
        return result

    def trace(self) -> dict:
        return json.loads((self.root / "cycle-3-qwen.json").read_text())

    def test_valid_provider_response_records_exact_input_and_validation(self) -> None:
        expected = decision()
        result, raw = self.ask(completion(json.dumps(expected)))
        self.assertEqual(result, expected)
        trace = self.trace()
        self.assertEqual(trace["raw"], raw)
        self.assertEqual(trace["validation"], {"status": "accepted"})
        self.assertEqual(trace["request"]["sha256"], hashlib.sha256(self.request.data).hexdigest())
        self.assertEqual(trace["schema"], subject.qwen_decision_schema())
        self.assertEqual(trace["inputs"]["metrics"], {"tracking_error": 0.1})
        self.assertFalse(trace["inputs"]["visual_evidence"]["supplied"])
        payload = json.loads(self.request.data)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertIn(json.dumps(subject.qwen_decision_schema(), indent=2), payload["messages"][0]["content"][0]["text"])
        self.assertEqual(trace["request"]["text"], payload["messages"][0]["content"][0]["text"])

    def test_contact_sheet_hash_identifies_bytes_sent(self) -> None:
        video = self.root / "cycle-3-rollout.mp4"
        image = self.root / "cycle-3-contact-sheet.jpg"
        image.write_bytes(b"mock JPEG bytes")
        self.ask(completion(json.dumps(decision())), video)
        visual = self.trace()["inputs"]["visual_evidence"]
        self.assertEqual(visual["sha256"], hashlib.sha256(image.read_bytes()).hexdigest())
        self.assertEqual(visual["bytes"], image.stat().st_size)
        self.assertTrue(visual["supplied"])
        self.assertEqual(len(json.loads(self.request.data)["messages"][0]["content"]), 2)

    def test_native_request_is_opt_in_and_still_locally_validated(self) -> None:
        self.supervisor.manifest["qwen_response_format"] = "json_schema"
        self.ask(completion(json.dumps(decision())))
        self.assertEqual(json.loads(self.request.data)["response_format"], subject.qwen_response_format("json_schema"))
        with self.assertRaises(subject.SupervisorError):
            self.ask(completion(json.dumps(decision(extra="provider ignored schema"))))
        self.assertEqual(self.trace()["validation"]["status"], "rejected")

    def test_reasoning_is_never_a_fallback_or_executable_decision(self) -> None:
        good = json.dumps(decision())
        for field in ("reasoning_content", "reasoning"):
            for content in (None, "", [], {}, "analysis " + good):
                with self.subTest(field=field, content=content):
                    with self.assertRaises(subject.SupervisorError):
                        self.ask(completion(content, **{field: good}))
                    self.assertNotIn("decision", self.trace())
        expected = decision(action="hold")
        actual, _ = self.ask(completion(json.dumps(expected), reasoning_content=good))
        self.assertEqual(actual, expected)

    def test_rejects_malformed_envelopes_as_supervisor_advisories(self) -> None:
        good = completion(json.dumps(decision()))
        variants = [None, [], {}, {"error": "provider error"}, {"choices": []},
                    {"choices": {}}, {"choices": [None]},
                    {"choices": good["choices"] * 2},
                    completion({"action": "continue"}),
                    completion([{"type": "text", "text": json.dumps(decision())}]),
                    completion(json.dumps(decision()), role="tool"),
                    completion(json.dumps(decision()), tool_calls=[{"name": "train"}]),
                    completion(json.dumps(decision()), function_call={"name": "train"}),
                    completion(json.dumps(decision()), function_call={}),
                    completion(json.dumps(decision()), tool_calls=False),
                    completion(json.dumps(decision()), refusal=False),
                    completion(json.dumps(decision()), refusal="cannot answer")]
        for finish in ("length", "tool_calls", "content_filter", None):
            item = completion(json.dumps(decision()))
            item["choices"][0]["finish_reason"] = finish
            variants.append(item)
        for item in variants:
            with self.subTest(item=item):
                with self.assertRaises(subject.SupervisorError):
                    self.ask(item)
                self.assertEqual(self.trace()["validation"]["status"], "rejected")
                self.assertNotIn("decision", self.trace())

    def test_rejects_invalid_outer_json_and_retains_bounded_evidence(self) -> None:
        for raw in (b'not JSON', b'{"choices": [], "choices": []}', b'{"x": NaN}',
                    b'\xff', b' ' * (subject.MAX_QWEN_RESPONSE_BYTES + 1)):
            with self.subTest(prefix=raw[:50]):
                with self.assertRaises(subject.SupervisorError):
                    self.ask(raw)
                trace = self.trace()
                self.assertEqual(trace["validation"]["status"], "rejected")
                self.assertLessEqual(len(trace["response_text"]), subject.MAX_QWEN_RESPONSE_BYTES)
        self.assertTrue(self.trace()["response_truncated"])

    def test_transport_errors_record_failure_without_schema_downgrade(self) -> None:
        self.supervisor.manifest["qwen_response_format"] = "json_schema"
        with patch.object(subject.urllib.request, "urlopen", side_effect=urllib.error.HTTPError(
            "http://localhost/mock", 400, "unsupported response_format", {}, None
        )) as provider:
            with self.assertRaisesRegex(subject.SupervisorError, "request failed"):
                self.supervisor.ask_qwen({}, None)
        provider.assert_called_once()
        self.assertEqual(self.trace()["validation"]["status"], "request_failed")
        self.assertEqual(self.trace()["request"]["response_format"]["type"], "json_schema")


class QwenDurationRegressionTests(unittest.TestCase):
    def exercise_cycles(
        self, provider_body: object, initial_tuning: dict | None = None,
        provider_error: Exception | None = None,
    ) -> dict:
        """Run the real controller loop using only mocked training/model boundaries."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = json.loads((_HERE / "robots" / "simple-dog.json").read_text())
            manifest.update(max_minutes=10, max_cycles=1, training_chunks_per_cycle=1,
                            evaluation_candidates_per_cycle=1, tuning=initial_tuning or {})
            manifest_path = root / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            supervisor = subject.Supervisor(manifest_path, dry_run=False)
            supervisor.run_dir = root / "run"
            supervisor.global_status = root / "status.json"
            experiment = root / "experiment"
            candidate = experiment / "nn" / "candidate.pth"
            video = root / "candidate-rollout.mp4"
            baseline = {"RoughRollout/forward_displacement": {"last": 0.0}}
            # No improvement: model prose must not change the deterministic gate.
            supervisor.require_free_gpu = MagicMock()
            supervisor.start_lab = MagicMock()
            supervisor.start_qwen = MagicMock()
            supervisor.stop_owned = MagicMock()
            supervisor.inspect_metrics = MagicMock(return_value={})
            supervisor.render_rollout = MagicMock(return_value=(baseline, video))
            supervisor.archive_best = MagicMock()
            supervisor.run_training_chunk = MagicMock(return_value=(root / "training", 300.0))
            supervisor.experiment_dir = MagicMock(return_value=experiment)
            supervisor.latest_training_checkpoint = MagicMock(return_value=candidate)
            supervisor.prepare_training_continuation = MagicMock()
            supervisor.evaluate = MagicMock(return_value=(candidate, {}, baseline, video))
            with (patch.object(subject, "HOST_ROOT", root),
                  patch.object(subject, "AUTORESEARCH_ROOT", _HERE),
                  patch.object(subject.os, "geteuid", return_value=1001),
                  patch.dict(subject.os.environ, {"USER": "leo"}),
                  patch.object(subject, "run", side_effect=AssertionError("Unexpected process")),
                  patch.object(subject.urllib.request, "urlopen", return_value=io.BytesIO(
                      json.dumps(provider_body).encode()),
                      side_effect=provider_error) as provider):
                self.assertEqual(supervisor.execute(), 0)
            self.assertEqual(supervisor.state["status"], "complete")
            self.assertEqual(supervisor.state["training_seconds_completed"], 600.0)
            self.assertEqual(supervisor.run_training_chunk.call_count, 2)
            self.assertEqual(supervisor.evaluate.call_count, 2)
            supervisor.archive_best.assert_called_once()  # Baseline only.
            self.assertEqual(provider.call_count, 1)  # Cap does not cap PPO duration.
            self.assertEqual(supervisor.state["checkpoint"], manifest["baseline_checkpoint"]
                             if "baseline_checkpoint" in manifest else manifest["checkpoint"])
            self.assertFalse((supervisor.run_dir / "codex_request.md").exists())
            result = dict(supervisor.state)
            result["first_evolution"] = json.loads((supervisor.run_dir / "cycle-1-evolution.json").read_text())
            return result

    def test_hold_escalate_invalid_and_refusal_finish_training_budget(self) -> None:
        for body in (completion(json.dumps(decision(action="hold", tuning_changes={"gait_reward_scale": 5.5}))),
                     completion(json.dumps(decision(action="escalate", codex_request="Review drift."))),
                     completion("not JSON"), {},
                     completion(json.dumps(decision(action="hold", summary="\ud800"))),
                     completion(json.dumps(decision(action="escalate", codex_request="\ud800"))),
                     completion('{"\\ud800":1,"\\ud800":2}'),
                     completion(json.dumps(decision(**{"\ud800": 1}))),
                     completion(None, refusal="cannot answer")):
            with self.subTest(body=body):
                state = self.exercise_cycles(body)
                self.assertIn("latest_advisory", state)
                self.assertEqual(state["tuning"], {})

    def test_provider_request_failure_finishes_training_budget(self) -> None:
        state = self.exercise_cycles(None, provider_error=urllib.error.URLError("offline"))
        self.assertEqual(state["tuning"], {})
        self.assertIn("request failed", state["latest_advisory_reason"])

    def test_valid_tuning_still_uses_existing_bounded_gate(self) -> None:
        state = self.exercise_cycles(completion(json.dumps(decision(tuning_changes={
            "gait_reward_scale": 5.5, "body_vel_reward_scale": 8.0
        }))))
        self.assertEqual(state["tuning"], {"gait_reward_scale": 5.5})
        self.assertTrue(any("25%" in note for note in state["first_evolution"]["validation_notes"]))

    def test_conflicting_tuning_is_advisory_without_partial_application(self) -> None:
        current = {"command_forward_min": 0.24, "command_forward_max": 0.25}
        state = self.exercise_cycles(completion(json.dumps(decision(tuning_changes={
            "command_forward_min": 0.30
        }))), current)
        self.assertEqual(state["tuning"], current)
        self.assertIn("exceeds command_forward_max", state["latest_advisory_reason"])
        self.assertEqual(state["first_evolution"]["tuning_before"], state["first_evolution"]["tuning_after"])


if __name__ == "__main__":
    unittest.main()
