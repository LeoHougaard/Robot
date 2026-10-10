"""Adversarial, dependency-free tests for the complete intent boundary."""
from dataclasses import FrozenInstanceError
import json
import unittest

from robot_llm.contract import (Clarify, ContractError, MAX_RESPONSE_CHARS,
                                StopRequest, Unsupported, VelocityRequest,
                                intent_schema, intent_to_dict, parse_intent)


def velocity(**updates):
    value = {"schema_version": 1, "kind": "velocity_request", "frame": "body",
             "forward_m_s": 0.04, "lateral_m_s": 0.0, "yaw_rate_rad_s": 0.0,
             "duration_ms": 500}
    return json.dumps(dict(value, **updates))


class ContractTests(unittest.TestCase):
    def test_round_trip_all_variants(self):
        intents = [Clarify("Which direction?"), Unsupported("No localization available"),
                   StopRequest(), VelocityRequest(0.04, -0.02, 0.1, 500)]
        for intent in intents:
            with self.subTest(intent=intent):
                self.assertEqual(parse_intent(json.dumps(intent_to_dict(intent))), intent)

    def test_strict_whole_object_only(self):
        samples = ["", "null", "true", "1", "[]", '[{"kind":"stop_request"}]',
                   "Here is the answer: " + velocity(), "```json\n" + velocity() + "\n```",
                   velocity() + velocity(), velocity() + " trailing", velocity()[:-1],
                   '{"schema_version":1,"kind":"stop_request",}',
                   '{"schema_version":1,"kind":"stop_request" /* comment */}',
                   " " * (MAX_RESPONSE_CHARS + 1), None, b"{}"]
        for raw in samples:
            with self.subTest(raw=repr(raw)[:100]):
                with self.assertRaises(ContractError):
                    parse_intent(raw)
        self.assertIsInstance(parse_intent(" \n" + velocity() + "\t"), VelocityRequest)

    def test_duplicate_keys_including_escaped_keys_are_rejected(self):
        for raw in ['{"schema_version":1,"schema_version":1,"kind":"stop_request"}',
                    '{"schema_version":1,"kind":"stop_request","kind":"clarify","question":"?"}',
                    '{"schema_version":1,"kind":"stop_request","k\\u0069nd":"stop_request"}',
                    '{"schema_version":1,"kind":"stop_request","x":{"a":1,"a":2}}']:
            with self.subTest(raw=raw), self.assertRaises(ContractError):
                parse_intent(raw)

    def test_unknown_and_missing_keys_are_rejected(self):
        for key, value in {"session_id": "forged", "request_id": "forged", "ttl_ms": 10_000,
                           "authorized": True, "arm": True, "extra": None,
                           "forward_cm_s": 4}.items():
            with self.subTest(key=key), self.assertRaises(ContractError):
                parse_intent(velocity(**{key: value}))
        value = json.loads(velocity())
        for key in value:
            incomplete = value.copy()
            del incomplete[key]
            with self.subTest(missing=key), self.assertRaises(ContractError):
                parse_intent(json.dumps(incomplete))

    def test_exact_version_discriminator_frame(self):
        for version in (0, 2, True, "1", 1.0, None):
            with self.subTest(version=version), self.assertRaises(ContractError):
                parse_intent(velocity(schema_version=version))
        for kind in ("execute", "navigate", "stand", "shell", "velocity", [], None):
            with self.subTest(kind=kind), self.assertRaises(ContractError):
                parse_intent(velocity(kind=kind))
        for frame in ("world", "camera", "body_cm", None, 1, True):
            with self.subTest(frame=frame), self.assertRaises(ContractError):
                parse_intent(velocity(frame=frame))

    def test_rejects_coercions_and_nonfinite_numbers_for_each_axis(self):
        for axis in ("forward_m_s", "lateral_m_s", "yaw_rate_rad_s"):
            for bad in ("0.04", "4 cm/s", True, False, None, [], {},
                        float("nan"), float("inf"), -float("inf"), 10 ** 400):
                with self.subTest(axis=axis, bad=str(bad)[:30]), self.assertRaises(ContractError):
                    parse_intent(velocity(**{axis: bad}))
        with self.assertRaises(ContractError):
            parse_intent(velocity().replace("0.04", "1e999"))

    def test_duration_is_bounded_strict_integer(self):
        for duration in (0, -1, 5_001, 1.0, 1.5, "500", True, None):
            with self.subTest(duration=duration), self.assertRaises(ContractError):
                parse_intent(velocity(duration_ms=duration))
        for duration in (1, 5_000):
            self.assertEqual(parse_intent(velocity(duration_ms=duration)).duration_ms, duration)

    def test_text_must_be_nonblank_bounded_string(self):
        for kind, key in (("clarify", "question"), ("unsupported", "reason")):
            for bad in ("", "   \n\t", "a" * 501, 1, True, None, []):
                with self.subTest(kind=kind, bad=bad), self.assertRaises(ContractError):
                    parse_intent(json.dumps({"schema_version": 1, "kind": kind, key: bad}))

    def test_no_plan_or_partial_step_repair(self):
        for plan in ([json.loads(velocity()), {"kind": "shell"}],
                     {"schema_version": 1, "kind": "plan", "steps": [json.loads(velocity())]}):
            with self.assertRaises(ContractError):
                parse_intent(json.dumps(plan))

    def test_direct_dataclasses_revalidated_before_serialization(self):
        for invalid in (VelocityRequest(True, 0.0, 0.0, 500),
                        VelocityRequest(float("nan"), 0.0, 0.0, 500), Clarify(""), object()):
            with self.assertRaises(ContractError):
                intent_to_dict(invalid)
        with self.assertRaises(FrozenInstanceError):
            StopRequest().kind = "velocity_request"

    def test_schema_is_exact_fresh_and_has_no_host_authority_fields(self):
        schema = intent_schema()
        self.assertEqual(len(schema["oneOf"]), 4)
        for variant in schema["oneOf"]:
            self.assertFalse(variant["additionalProperties"])
            self.assertEqual(set(variant["required"]), set(variant["properties"]))
            self.assertNotIn("authorized", variant["properties"])
            self.assertNotIn("expires_at_ms", variant["properties"])
        schema["oneOf"].clear()
        self.assertEqual(len(intent_schema()["oneOf"]), 4)

    def test_unpaired_unicode_surrogates_rejected_before_reporting(self):
        for bad in ("\ud800", "\udfff", "bad\udc00text"):
            for kind, key in (("clarify", "question"), ("unsupported", "reason")):
                raw = json.dumps({"schema_version": 1, "kind": kind, key: bad})
                with self.subTest(raw=raw), self.assertRaises(ContractError):
                    parse_intent(raw)
            with self.assertRaises(ContractError):
                parse_intent('{"schema_version":1,"kind":"stop_request","' + bad + '":0}')
            with self.assertRaises(ContractError):
                parse_intent('{"schema_version":1,"kind":"stop_request",' + json.dumps(bad) + ':0}')
        question = "Go left? \U0001f916"
        self.assertEqual(parse_intent(json.dumps({"schema_version": 1, "kind": "clarify",
                                                "question": question})).question, question)

    def test_capability_limits_are_not_invented_by_the_parser(self):
        # Well-formed is not admitted: the independently supplied capability
        # snapshot is responsible for rejecting these large values.
        self.assertEqual(parse_intent(velocity(forward_m_s=1_000)).forward_m_s, 1_000)

    def test_excessive_nesting_fails_closed(self):
        with self.assertRaises(ContractError):
            parse_intent('{"x":' + "[" * 2_000 + "0" + "]" * 2_000 + "}")


if __name__ == "__main__":
    unittest.main()
