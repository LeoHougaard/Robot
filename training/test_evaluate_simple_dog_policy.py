"""Unit tests for command-specific deterministic mobility gates."""

from __future__ import annotations

import math
import unittest

from evaluate_simple_dog_policy import EXPECTED, evaluate


COMMANDS = {
    "stand": (0.0, 0.0, 0.0),
    "forward": (0.22, 0.0, 0.0),
    "reverse": (-0.18, 0.0, 0.0),
    "strafe_left": (0.0, 0.16, 0.0),
    "strafe_right": (0.0, -0.16, 0.0),
    "turn_left": (0.0, 0.0, 0.25),
    "turn_right": (0.0, 0.0, -0.25),
    "diagonal_left": (0.16, 0.12, 0.0),
    "diagonal_right": (0.16, -0.12, 0.0),
    "diagonal_reverse_left": (-0.14, 0.12, 0.0),
    "diagonal_reverse_right": (-0.14, -0.12, 0.0),
    "curve_left": (0.16, 0.08, 0.25),
    "curve_right": (0.16, -0.08, -0.25),
    "stop": (0.0, 0.0, 0.0),
}


def ideal_segment(name: str, *, step_dt: float | None = None) -> dict:
    forward, lateral, yaw = COMMANDS.get(name, (0.14, 0.0, 0.0))
    stationary = name in {"stand", "stop"}
    steps = 100 if name in {"stand", "stop"} else 175
    duration = steps * (0.02 if step_dt is None else step_dt)
    segment = {
        "name": name,
        "steps": steps,
        "command_forward": forward,
        "command_lateral": lateral,
        "command_yaw": yaw,
        "mean_body_forward": forward,
        "mean_body_lateral": lateral,
        "mean_abs_body_lateral": abs(lateral),
        "mean_yaw_rate": yaw,
        "mean_foot_slip": 0.05,
        "mean_swing_foot_clearance": 0.0 if stationary else 0.025,
        "mean_action_rate": 0.10,
        "max_action_step": 0.20,
        "mean_abs_hip_abduction": 0.08,
        "max_abs_hip_abduction": 0.16,
        "mean_abs_vertical_speed": 0.03,
        "mean_tilt": 0.03,
        "forward_displacement": forward * duration,
        "lateral_displacement": lateral * duration,
        "heading_delta": math.remainder(yaw * duration, 2.0 * math.pi),
        "min_height": 0.18,
        "swing_fraction_frflbrbl": [0.0, 0.0, 0.0, 0.0] if stationary else [0.45, 0.45, 0.45, 0.45],
        "landings_frflbrbl": [0, 0, 0, 0] if stationary else [4, 4, 4, 4],
        "resets": 0,
        "mean_abs_height_error": 0.010,
        "mean_abs_roll_error": 0.030,
        "mean_abs_pitch_error": 0.030,
        "mean_current_valid_fraction": 0.8 if name == "current_dropout_walk" else 1.0,
    }
    if step_dt is not None:
        segment["step_dt"] = step_dt
    return segment


class MobilityEvaluationTests(unittest.TestCase):
    def test_ideal_full_mobility_suite_passes(self):
        segments = {name: ideal_segment(name) for name in EXPECTED["goal"]}
        self.assertTrue(evaluate("goal", segments)["passed"])

    def test_rough_uses_full_mobility_suite_and_requires_gait_quality(self):
        self.assertEqual(EXPECTED["rough"], EXPECTED["goal"])
        segments = {name: ideal_segment(name) for name in EXPECTED["rough"]}
        self.assertTrue(evaluate("rough", segments)["passed"])
        segments["forward"]["mean_foot_slip"] = 0.31
        result = evaluate("rough", segments)
        self.assertFalse(result["passed"])
        self.assertTrue(any("mean foot slip" in failure for failure in result["failures"]))

    def test_current_body_uses_mobility_without_old_posture_gates(self):
        self.assertEqual(EXPECTED["currentbody"], EXPECTED["goal"])
        segments = {name: ideal_segment(name) for name in EXPECTED["currentbody"]}
        for segment in segments.values():
            segment.pop("mean_abs_height_error")
            segment.pop("mean_abs_roll_error")
            segment.pop("mean_abs_pitch_error")
        self.assertTrue(
            evaluate("currentbody", segments, require_gait_quality=True)["passed"]
        )

    def test_emitted_25_hz_step_duration_controls_heading_math(self):
        segments = {
            name: ideal_segment(name, step_dt=0.04) for name in EXPECTED["goal"]
        }
        self.assertTrue(evaluate("goal", segments)["passed"])

    def test_wrong_direction_reverse_fails(self):
        segments = {name: ideal_segment(name) for name in EXPECTED["goal"]}
        segments["reverse"]["mean_body_forward"] = 0.12
        segments["reverse"]["forward_displacement"] = 0.42
        result = evaluate("goal", segments)
        self.assertFalse(result["passed"])
        self.assertTrue(any("reverse:" in failure for failure in result["failures"]))

    def test_wrong_direction_strafe_fails(self):
        segments = {name: ideal_segment(name) for name in EXPECTED["goal"]}
        segments["strafe_left"]["mean_body_lateral"] = -0.10
        result = evaluate("goal", segments)
        self.assertFalse(result["passed"])
        self.assertTrue(any("strafe_left:" in failure for failure in result["failures"]))

    def test_stationary_foot_parked_aloft_fails(self):
        segments = {name: ideal_segment(name) for name in EXPECTED["goal"]}
        segments["stop"]["swing_fraction_frflbrbl"][2] = 0.90
        segments["stop"]["mean_swing_foot_clearance"] = 0.025
        result = evaluate("goal", segments)
        self.assertFalse(result["passed"])
        self.assertTrue(any("parked aloft" in failure for failure in result["failures"]))

    def test_stationary_rough_ground_micro_gap_passes(self):
        segments = {name: ideal_segment(name) for name in EXPECTED["goal"]}
        segments["stop"]["swing_fraction_frflbrbl"][0] = 0.95
        segments["stop"]["mean_swing_foot_clearance"] = 0.001
        self.assertTrue(evaluate("goal", segments)["passed"])

    def test_moving_body_tilt_fails(self):
        segments = {name: ideal_segment(name) for name in EXPECTED["goal"]}
        segments["forward"]["mean_tilt"] = 0.16
        result = evaluate("goal", segments)
        self.assertFalse(result["passed"])
        self.assertTrue(any("mean body tilt" in failure for failure in result["failures"]))

    def test_splayed_moving_stance_fails_gait_quality(self):
        segments = {name: ideal_segment(name) for name in EXPECTED["goal"]}
        segments["forward"]["mean_abs_hip_abduction"] = 0.23
        segments["forward"]["max_abs_hip_abduction"] = 0.30
        result = evaluate("goal", segments, require_gait_quality=True)
        self.assertFalse(result["passed"])
        self.assertTrue(
            any("hip abduction" in failure for failure in result["failures"])
        )

    def test_current_suite_passes_and_gates_posture_and_dropout(self):
        segments = {name: ideal_segment(name) for name in EXPECTED["current"]}
        self.assertTrue(evaluate("current", segments, require_gait_quality=True)["passed"])
        segments["crouch_walk"]["mean_abs_height_error"] = 0.040
        segments["current_dropout_walk"]["mean_current_valid_fraction"] = 1.0
        result = evaluate("current", segments, require_gait_quality=True)
        self.assertFalse(result["passed"])
        self.assertTrue(any("height error" in failure for failure in result["failures"]))
        self.assertTrue(any("current validity" in failure for failure in result["failures"]))


if __name__ == "__main__":
    unittest.main()
