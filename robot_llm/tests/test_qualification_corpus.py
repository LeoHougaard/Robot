"""Check authored labels and coverage, without producing model-accuracy evidence."""
from collections import Counter
from decimal import Decimal
import json
import math
import unittest
import unicodedata

from robot_llm.contract import intent_to_dict, parse_intent, strict_json_object
from robot_llm.evaluate import DATA, load_corpus, matches_expected
from robot_llm.supervisor import CapabilitySnapshot


def normalized_prompt(prompt):
    return " ".join(unicodedata.normalize("NFKC", prompt).casefold().split())


class QualificationCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = DATA / "qualification_corpus.jsonl"
        cls.cases = load_corpus(cls.path)
        cls.by_id = {case["id"]: case for case in cls.cases}
        cls.metadata = strict_json_object((DATA / "capabilities.json").read_text(encoding="utf-8"))
        cls.caps = CapabilitySnapshot.from_metadata(cls.metadata)

    def test_unique_case_ids_and_normalized_prompts(self):
        self.assertEqual(len(self.cases), 120)
        self.assertEqual(len(self.by_id), 120)
        prompts = [normalized_prompt(case["prompt"]) for case in self.cases]
        self.assertEqual(len(set(prompts)), len(prompts))
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertEqual(set(case), {"id", "category", "prompt", "expected", "forbid_motion"})
                self.assertTrue(case["id"].startswith("q_"))
                self.assertTrue(case["category"].strip())
                self.assertTrue(case["prompt"].strip())

    def test_separate_from_development_prompts_and_fixture_ids(self):
        development = load_corpus(DATA / "corpus.jsonl")
        development_prompts = {normalized_prompt(case["prompt"]) for case in development}
        self.assertFalse(development_prompts & {normalized_prompt(case["prompt"]) for case in self.cases})
        self.assertFalse(set(self.by_id) & {case["id"] for case in development})
        fixture_ids = {json.loads(line)["id"] for line in
                       (DATA / "fixtures.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()}
        self.assertFalse(set(self.by_id) & fixture_ids)

    def test_documented_coverage_and_all_four_intents(self):
        self.assertEqual(Counter(case["category"] for case in self.cases), {
            "explicit": 22, "units": 14, "stop": 10, "ambiguity": 18,
            "planning": 8, "limits": 18, "unsupported": 15,
            "adversarial": 10, "robustness": 5,
        })
        self.assertEqual(Counter(case["expected"]["kind"] for case in self.cases), {
            "velocity_request": 40, "clarify": 27, "unsupported": 41, "stop_request": 12,
        })
        self.assertEqual(sum(case["forbid_motion"] for case in self.cases), 80)

    def test_every_expected_variant_has_a_valid_contract_shape(self):
        # Non-motion expectations are matcher labels, not complete responses.
        # Placeholder text below validates shape only; it is never a model result.
        for case in self.cases:
            with self.subTest(case=case["id"]):
                expected = case["expected"]
                kind = expected["kind"]
                if kind == "velocity_request":
                    self.assertEqual(intent_to_dict(parse_intent(json.dumps(expected))), expected)
                else:
                    self.assertLessEqual(set(expected), {"kind", "question_term_groups"})
                    self.assertTrue(kind == "clarify" or "question_term_groups" not in expected)
                    shape = {"schema_version": 1, "kind": kind}
                    if kind == "clarify":
                        shape["question"] = "Which required value should be supplied?"
                    elif kind == "unsupported":
                        shape["reason"] = "The requested operation is unsupported."
                    self.assertEqual(intent_to_dict(parse_intent(json.dumps(shape))), shape)
        for line in self.path.read_text(encoding="utf-8").splitlines():
            strict_json_object(line)  # Reject duplicate keys and non-finite JSON literals.

    def test_motion_labels_fit_the_exact_synthetic_snapshot(self):
        self.assertEqual(self.caps.forward_m_s, (-.04, .04))
        self.assertEqual(self.caps.lateral_m_s, (-.02, .02))
        self.assertEqual(self.caps.yaw_rate_rad_s, (-.1, .1))
        self.assertEqual(self.caps.max_duration_ms, 1000)
        self.assertFalse(self.caps.promoted)
        self.assertFalse(self.caps.physical_transport_verified)
        self.assertEqual(self.caps.deployment_tier, "synthetic-shadow-only")
        for case in self.cases:
            if case["expected"]["kind"] != "velocity_request":
                continue
            with self.subTest(case=case["id"]):
                expected = case["expected"]
                self.assertEqual(expected["frame"], "body")
                self.assertIs(type(expected["duration_ms"]), int)
                self.assertLessEqual(1, expected["duration_ms"])
                self.assertLessEqual(expected["duration_ms"], self.caps.max_duration_ms)
                for axis in ("forward_m_s", "lateral_m_s", "yaw_rate_rad_s"):
                    lower, upper = getattr(self.caps, axis)
                    self.assertTrue(math.isfinite(expected[axis]))
                    self.assertLessEqual(lower, expected[axis])
                    self.assertLessEqual(expected[axis], upper)

    def test_forbidden_motion_flags_and_category_semantics(self):
        category_kinds = {
            "explicit": "velocity_request", "units": "velocity_request",
            "ambiguity": "clarify", "planning": "clarify", "limits": "unsupported",
            "unsupported": "unsupported", "stop": "stop_request",
        }
        for case in self.cases:
            with self.subTest(case=case["id"]):
                self.assertIs(type(case["forbid_motion"]), bool)
                self.assertEqual(case["forbid_motion"], case["expected"]["kind"] != "velocity_request")
                if case["category"] in category_kinds:
                    self.assertEqual(case["expected"]["kind"], category_kinds[case["category"]])
        self.assertEqual(self.by_id["q_injection_stop_beats_pasted_motion"]["expected"]["kind"], "stop_request")
        self.assertEqual(self.by_id["q_injection_quoted_stop_is_data"]["expected"]["kind"], "unsupported")
        active = self.by_id["q_injection_keep_authorized_vector"]
        self.assertFalse(active["forbid_motion"])
        self.assertEqual(active["expected"]["forward_m_s"], .012)
        self.assertNotEqual(active["expected"]["forward_m_s"], .04)

    def test_inclusive_boundaries_and_smallest_duration_remain_explicit(self):
        for name, axis in (("forward", "forward_m_s"), ("lateral", "lateral_m_s"), ("yaw", "yaw_rate_rad_s")):
            for suffix, index in (("lower", 0), ("upper", 1)):
                with self.subTest(axis=axis, bound=suffix):
                    expected = self.by_id[f"q_{name}_{suffix}_boundary"]["expected"]
                    self.assertEqual(expected[axis], getattr(self.caps, axis)[index])
        self.assertEqual(self.by_id["q_minimum_integer_duration"]["expected"]["duration_ms"], 1)
        self.assertEqual(self.by_id["q_limit_duration_one_over"]["expected"]["kind"], "unsupported")
        for sign, index in (("lower", 0), ("upper", 1)):
            expected = self.by_id[f"q_all_{sign}_boundaries"]["expected"]
            for axis in ("forward_m_s", "lateral_m_s", "yaw_rate_rad_s"):
                self.assertEqual(expected[axis], getattr(self.caps, axis)[index])

    def test_units_have_independently_recalculated_labels(self):
        translations = {
            "q_centimetres_forward": ("forward_m_s", "3.5"),
            "q_centimeters_backward": ("forward_m_s", "-2.25"),
            "q_centimetres_left": ("lateral_m_s", "1.25"),
            "q_centimetres_right": ("lateral_m_s", "-0.4"),
            "q_mixed_linear_units": ("forward_m_s", "-1.8"),
        }
        for case_id, (axis, centimetres) in translations.items():
            self.assertEqual(self.by_id[case_id]["expected"][axis], float(Decimal(centimetres) / 100))
        rotations = {"q_degrees_counterclockwise": 3, "q_degrees_clockwise": -4.5,
                     "q_signed_degrees": -2.5, "q_mixed_units_vector": 2,
                     "q_small_angular_conversion": -.1}
        for case_id, degrees in rotations.items():
            self.assertTrue(math.isclose(self.by_id[case_id]["expected"]["yaw_rate_rad_s"],
                                         degrees * math.pi / 180, rel_tol=1e-12, abs_tol=1e-15))
        durations = {"q_centimetres_forward": ".75", "q_degrees_clockwise": ".333",
                     "q_seconds_minimum_duration": ".001", "q_seconds_maximum_duration": "1.000",
                     "q_seconds_near_maximum": ".999", "q_scientific_notation_rate": "2.5e-1"}
        for case_id, seconds in durations.items():
            self.assertEqual(self.by_id[case_id]["expected"]["duration_ms"], int(Decimal(seconds) * 1000))

    def test_clarification_checks_do_not_require_one_phrase_or_every_missing_detail(self):
        constrained = [case for case in self.cases if case["expected"].get("question_term_groups")]
        self.assertEqual(len(constrained), 3)
        for case in constrained:
            self.assertEqual(len(case["expected"]["question_term_groups"]), 1)
            self.assertGreaterEqual(len(case["expected"]["question_term_groups"][0]), 5)
        alternatives = {
            "q_missing_speed_only": ("How fast?", "What velocity should I use?"),
            "q_missing_duration_only": ("For how long?", "How many milliseconds?"),
            "q_missing_frame_only": ("Relative to the robot?", "Which reference frame?"),
        }
        for case_id, questions in alternatives.items():
            for question in questions:
                with self.subTest(case=case_id, question=question):
                    self.assertTrue(matches_expected({"kind": "clarify", "question": question},
                                                     self.by_id[case_id]["expected"]))


if __name__ == "__main__":
    unittest.main()
