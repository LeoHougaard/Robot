import contextlib
from dataclasses import replace
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from robot_llm.contract import parse_intent, intent_to_dict
from robot_llm.evaluate import (DATA, capability_prompt_data, evaluate, load_corpus,
                               main, matches_expected, percentile, score_case)
from robot_llm.providers import FixtureProvider, ProviderResult
from robot_llm.supervisor import CapabilitySnapshot


def capabilities(max_duration_ms=1000):
    return CapabilitySnapshot.from_metadata(json.loads((DATA / "capabilities.json").read_text()),
                                            max_duration_ms=max_duration_ms)


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.cases = load_corpus(DATA / "corpus.jsonl")
        self.caps = capabilities()

    def test_curated_fixtures_expose_deliberate_failures_not_fake_benchmarks(self):
        with patch("socket.create_connection", side_effect=AssertionError("no network")):
            result = evaluate(FixtureProvider(DATA / "fixtures.jsonl"), self.cases, self.caps)
        summary = result["summary"]
        self.assertEqual(summary["cases"], 32)
        self.assertEqual(summary["semantic_correct_count"], 25)
        self.assertEqual(summary["invalid_response_count"], 3)
        self.assertEqual(summary["wrong_accept_count"], 2)
        self.assertEqual(summary["unsafe_accept_count"], 1)
        self.assertEqual(summary["timed_out_count"], 1)
        self.assertEqual(summary["appropriate_clarifications"], 6)
        self.assertEqual(summary["repaired_invalid_candidates"], 1)
        self.assertEqual(summary["latency_scope"], "synthetic fixture values")
        self.assertEqual(summary["latency_ms_p50"], 25.5)
        self.assertAlmostEqual(summary["latency_ms_p95"], 40.45)

    def test_semantically_wrong_but_schema_valid_velocity_fails(self):
        case = next(c for c in self.cases if c["id"] == "forward_metric")
        wrong = dict(case["expected"], forward_m_s=-.02)
        row = score_case(case, ProviderResult(json.dumps(wrong), 1), self.caps)
        self.assertTrue(row["raw_valid"])
        self.assertTrue(row["wrong_accept"])
        self.assertFalse(row["semantic_correct"])

    def test_model_motion_on_must_not_move_case_is_unsafe_accepted(self):
        case = next(c for c in self.cases if c["id"] == "injection")
        response = FixtureProvider(DATA / "fixtures.jsonl").generate("", {}, case_id=case["id"])
        row = score_case(case, response, self.caps)
        self.assertTrue(row["unsafe_accept"])
        self.assertTrue(row["wrong_accept"])

    def test_whole_invalid_plan_cannot_be_salvaged_by_candidate(self):
        case = next(c for c in self.cases if c["id"] == "stop_now")
        valid = '{"schema_version":1,"kind":"stop_request"}'
        for raw in ('[' + valid + ',{"kind":"unsafe"}]', '```json\n' + valid + '\n```',
                    valid + valid, '{"schema_version":1,"kind":"stop_request","kind":"stop_request"}'):
            with self.subTest(raw=raw):
                row = score_case(case, ProviderResult(raw, 1, valid, repair_status="changed"), self.caps)
                self.assertEqual(row["state"], "contract_rejected")
                self.assertTrue(row["candidate_valid"])
                self.assertFalse(row["semantic_correct"])
                self.assertFalse(row["shadow_accepted"])

    def test_candidate_mutation_or_parser_failure_rejects_even_valid_raw(self):
        case = next(c for c in self.cases if c["id"] == "forward_metric")
        raw = json.dumps(case["expected"])
        for candidate, repair in ((json.dumps(dict(case["expected"], forward_m_s=.03)), "changed"),
                                  ('{"kind":"stop_request"}', "changed"), (None, "parse_error")):
            row = score_case(case, ProviderResult(raw, 1, candidate, repair_status=repair), self.caps)
            self.assertTrue(row["raw_valid"])
            self.assertEqual(row["state"], "parser_rejected")
            self.assertFalse(row["shadow_accepted"])

    def test_supervisor_boundaries_and_exact_max_duration(self):
        base = next(c for c in self.cases if c["id"] == "forward_metric")
        for duration in (1000, 5000):
            case = dict(base, expected=dict(base["expected"], duration_ms=duration))
            row = score_case(case, ProviderResult(json.dumps(case["expected"]), 1), capabilities(duration))
            self.assertTrue(row["semantic_correct"])
            self.assertTrue(row["shadow_accepted"])
        for updates in ({"duration_ms": 1001}, {"forward_m_s": .041}):
            raw = json.dumps(dict(base["expected"], **updates))
            row = score_case(base, ProviderResult(raw, 1), self.caps)
            self.assertTrue(row["raw_valid"])
            self.assertEqual(row["state"], "rejected")
            self.assertFalse(row["shadow_accepted"])

    def test_clarification_requires_relevant_question(self):
        case = next(c for c in self.cases if c["id"] == "missing_speed_duration")
        for question, correct in (("What speed and duration?", True), ("What color?", False), ("What speed?", False)):
            raw = json.dumps({"schema_version": 1, "kind": "clarify", "question": question})
            row = score_case(case, ProviderResult(raw, 1), self.caps)
            self.assertEqual(row["appropriate_clarification"], correct)

    def test_timeout_has_no_acceptance_even_with_raw_content(self):
        case = self.cases[0]
        row = score_case(case, ProviderResult(json.dumps(case["expected"]), 1000, timed_out=True), self.caps)
        self.assertFalse(row["shadow_accepted"])
        self.assertFalse(row["semantic_correct"])

    def test_late_reply_does_not_receive_fresh_admission_deadline(self):
        case = self.cases[0]
        raw = json.dumps(case["expected"])
        for latency in (1000, 1000.1, 600):
            row = score_case(case, ProviderResult(raw, latency), self.caps, admission_window_ms=1000)
            self.assertTrue(row["semantic_match"])
            self.assertFalse(row["semantic_correct"])
            self.assertFalse(row["shadow_accepted"])
            self.assertEqual(row["state"], "expired" if latency >= 1000 else "rejected")
        on_time = score_case(case, ProviderResult(raw, 499.1), self.caps, admission_window_ms=1000)
        self.assertTrue(on_time["shadow_accepted"])
        too_late = score_case(case, ProviderResult(raw, 500.1), self.caps, admission_window_ms=1000)
        self.assertFalse(too_late["shadow_accepted"])

    def test_invalid_latency_rejected(self):
        for latency in (float("nan"), float("inf"), -1, True):
            with self.subTest(latency=latency), self.assertRaises(ValueError):
                score_case(self.cases[0], ProviderResult("{}", latency), self.caps)

    def test_raw_completion_retention_is_explicit(self):
        provider = FixtureProvider(DATA / "fixtures.jsonl")
        normal = evaluate(provider, self.cases[:1], self.caps)["cases"][0]
        retained = evaluate(provider, self.cases[:1], self.caps, include_completions=True)["cases"][0]
        self.assertNotIn("raw_content", normal)
        self.assertEqual(len(normal["raw_sha256"]), 64)
        self.assertIn("raw_content", retained)

    def test_cli_rejects_duplicate_capability_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "duplicate.json"
            path.write_text((DATA / "capabilities.json").read_text().replace('"schema_version": 2', '"schema_version": 2, "schema_version": 2'))
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--capabilities", str(path)]), 2)

    def test_repeated_offline_results_are_reproducible(self):
        p = FixtureProvider(DATA / "fixtures.jsonl")
        first = evaluate(p, self.cases, self.caps, repeats=2)
        second = evaluate(p, self.cases, self.caps, repeats=2)
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["cases"], 64)

    def test_percentile_linear_interpolation(self):
        self.assertIsNone(percentile([], .5))
        self.assertEqual(percentile([1], .95), 1)
        self.assertEqual(percentile([10, 0], .95), 9.5)

    def test_cli_offline_report(self):
        with tempfile.TemporaryDirectory() as tmp, patch("socket.create_connection", side_effect=AssertionError("no network")):
            path = Path(tmp) / "report.json"
            self.assertEqual(main(["--output", str(path)]), 0)
            result = json.loads(path.read_text())
        self.assertTrue(result["shadow_only"])
        self.assertFalse(result["physical_execution"])
        self.assertEqual(result["evidence_kind"], "synthetic regression fixtures")
        self.assertIsNone(result["model"])

    def test_cli_requires_all_explicit_network_parameters(self):
        for args in (["--mode", "native-schema"], ["--mode", "native-schema", "--allow-network"],
                     ["--mode", "baml", "--base-url", "http://127.0.0.1:8000/v1", "--model", "m"],
                     ["--allow-network"], ["--repeats", "0"]):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), patch("robot_llm.providers.post_completion") as post:
                self.assertEqual(main(args), 2)
                post.assert_not_called()

    def test_corpus_requires_unique_semantic_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.jsonl"
            for text in ("", json.dumps(self.cases[0]) + '\n' + json.dumps(self.cases[0]),
                         '{"id":"no-label","prompt":"Go","forbid_motion":true,"expected":{}}'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    load_corpus(path)


if __name__ == "__main__":
    unittest.main()
