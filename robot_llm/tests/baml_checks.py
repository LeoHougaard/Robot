"""Explicit optional integration suite: real pinned generated parser, no inference.

Run only after installing requirements-baml.txt and baml-cli generate:
python -m unittest robot_llm.tests.baml_checks
Not test_*.py: the default stdlib suite needs no optional dependencies or skips.
"""
from importlib import metadata
import json
import unittest
from unittest.mock import patch

from robot_llm.contract import intent_to_dict, parse_intent
from robot_llm.evaluate import DATA, load_corpus, score_case
from robot_llm.providers import (BAML_VERSION, EndpointConfig, InferenceProvider,
                                ProviderResult, build_payload, observe_baml_parse)
from robot_llm.tests.test_evaluate import capabilities
from robot_llm.tests.test_providers import CAPS, STOP, envelope


class GeneratedBamlChecks(unittest.TestCase):
    def test_exact_installed_toolchain(self):
        self.assertEqual(metadata.version("baml-py"), BAML_VERSION)
        self.assertEqual(metadata.version("pydantic"), "2.11.7")

    def test_generated_request_only_targets_explicit_endpoint(self):
        config = EndpointConfig("http://127.0.0.1:8000/v1", "configured-model")
        with patch("socket.create_connection", side_effect=AssertionError("no network")):
            request = build_payload("baml", config, "Stop now.", CAPS)
        self.assertEqual(request["model"], "configured-model")
        self.assertIs(request["stream"], False)
        self.assertEqual(request["n"], 1)
        self.assertEqual(request["temperature"], 0)
        self.assertEqual(request["messages"][-1], {"role": "user", "content": "Stop now."})
        # Schema prompting/SAP is not provider-constrained decoding.
        self.assertNotIn("response_format", request)
        self.assertNotIn("structured_outputs", request)

    def test_all_valid_intent_variants_roundtrip_without_mutation(self):
        cases = [STOP,
            '{"schema_version":1,"kind":"clarify","question":"Which direction?"}',
            '{"schema_version":1,"kind":"unsupported","reason":"Stairs are unsupported."}',
            '{"schema_version":1,"kind":"velocity_request","frame":"body","forward_m_s":0.02,"lateral_m_s":0,"yaw_rate_rad_s":0,"duration_ms":500}']
        for raw in cases:
            with self.subTest(raw=raw):
                candidate, repair = observe_baml_parse(raw)
                self.assertEqual(repair, "unchanged")
                self.assertEqual(intent_to_dict(parse_intent(raw)), intent_to_dict(parse_intent(candidate)))

    def test_real_sap_fence_repair_is_observed_and_rejected(self):
        raw = "```json\n" + STOP + "\n```"
        candidate, repair = observe_baml_parse(raw)
        self.assertEqual(repair, "changed")
        self.assertIsNotNone(candidate)
        case = next(c for c in load_corpus(DATA / "corpus.jsonl") if c["id"] == "stop_now")
        row = score_case(case, ProviderResult(raw, 1, candidate, repair_status=repair), capabilities())
        self.assertTrue(row["candidate_valid"])
        self.assertEqual(row["state"], "contract_rejected")

    def test_numeric_string_repair_cannot_enter_shadow_supervisor(self):
        case = next(c for c in load_corpus(DATA / "corpus.jsonl") if c["id"] == "forward_metric")
        raw = json.dumps(dict(case["expected"], duration_ms="500"))
        candidate, repair = observe_baml_parse(raw)
        row = score_case(case, ProviderResult(raw, 1, candidate, repair_status=repair), capabilities())
        self.assertEqual(row["state"], "contract_rejected")
        self.assertFalse(row["shadow_accepted"])

    def test_baml_never_executes_network_or_uses_reasoning_fallback(self):
        config = EndpointConfig("http://127.0.0.1:8000/v1", "model")
        with patch("socket.create_connection", side_effect=AssertionError("no network")):
            result = InferenceProvider("baml", config, transport=lambda *_: envelope()).generate("Stop.", CAPS)
            self.assertEqual(result.repair_status, "unchanged")
            self.assertEqual(result.raw_content, STOP)
            bad = InferenceProvider("baml", config, transport=lambda *_: envelope(None, reasoning_content=STOP)).generate("Stop.", CAPS)
            self.assertIsNone(bad.raw_content)
            self.assertIsNotNone(bad.error)

    def test_stale_generated_sources_fail_closed(self):
        from robot_llm.providers import ProviderError
        from robot_llm.baml_client import inlinedbaml
        with patch.object(inlinedbaml, "get_baml_files", return_value={}):
            with self.assertRaisesRegex(ProviderError, "stale"):
                observe_baml_parse(STOP)

    def test_crlf_generated_sources_match_without_accepting_content_changes(self):
        from robot_llm.baml_client import inlinedbaml
        original = inlinedbaml.get_baml_files()
        crlf = {name: text.replace("\r\n", "\n").replace("\n", "\r\n") for name, text in original.items()}
        with patch.object(inlinedbaml, "get_baml_files", return_value=crlf):
            self.assertEqual(observe_baml_parse(STOP)[1], "unchanged")
        changed = dict(crlf)
        changed[next(iter(changed))] += "\n// substantive drift\n"
        with patch.object(inlinedbaml, "get_baml_files", return_value=changed):
            from robot_llm.providers import ProviderError
            with self.assertRaisesRegex(ProviderError, "stale"):
                observe_baml_parse(STOP)

    def test_version_mismatch_fails_before_build_or_network(self):
        with patch("robot_llm.providers.metadata.version", return_value="0.0.0"):
            from robot_llm.providers import ProviderError
            with self.assertRaisesRegex(ProviderError, "does not match"):
                build_payload("baml", EndpointConfig("http://127.0.0.1:8000/v1", "m"), "Stop.", CAPS)


if __name__ == "__main__":
    unittest.main()
