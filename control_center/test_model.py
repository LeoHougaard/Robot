from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import unittest

from control_center.model import load_profile, profile_hash, validate_profile


PROFILES = Path(__file__).parent / "profiles"


class ControlProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.known_good = load_profile(PROFILES / "isaaclab-unitree-go2-12dof.json")
        cls.template = load_profile(PROFILES / "custom-12dof-template.json")
        cls.assembly = load_profile(PROFILES / "assembly-1-12dof.json")
        cls.inward_knee = load_profile(PROFILES / "assembly-1-inward-knee-12dof.json")

    def test_known_good_is_launchable(self) -> None:
        result = validate_profile(self.known_good, for_launch=True)
        self.assertEqual([], result["errors"])

    def test_assembly_robot_is_launchable(self) -> None:
        result = validate_profile(self.assembly, for_launch=True)
        self.assertEqual([], result["errors"])
        self.assertEqual(12, len(self.assembly["robot"]["joints"]))

    def test_inward_knee_profile_is_launchable_and_sign_locked(self) -> None:
        result = validate_profile(self.inward_knee, for_launch=True)
        self.assertEqual([], result["errors"])
        changed = deepcopy(self.inward_knee)
        changed["robot"]["joints"][2]["direction"] = 1
        result = validate_profile(changed, for_launch=True)
        self.assertTrue(any("direction contract" in error for error in result["errors"]))

    def test_unvalidated_template_cannot_launch(self) -> None:
        result = validate_profile(self.template, for_launch=True)
        self.assertTrue(any("not marked ready" in error for error in result["errors"]))
        self.assertTrue(any("template values" in error for error in result["errors"]))

    def test_marking_template_ready_does_not_bypass_placeholders(self) -> None:
        profile = deepcopy(self.template)
        profile["robot"]["ready_for_training"] = True
        result = validate_profile(profile, for_launch=True)
        self.assertTrue(any("template values" in error for error in result["errors"]))

    def test_non_integral_control_decimation_is_rejected(self) -> None:
        profile = deepcopy(self.known_good)
        profile["environment"]["control_hz"] = 60
        result = validate_profile(profile)
        self.assertTrue(any("divide" in error for error in result["errors"]))

    def test_rough_surface_requires_rough_stage(self) -> None:
        profile = deepcopy(self.known_good)
        profile["training"]["stage"] = "V2Core"
        profile["environment"]["surface"] = "Slopes"
        result = validate_profile(profile)
        self.assertTrue(any("flat-ground" in error for error in result["errors"]))

    def test_hash_ignores_json_formatting(self) -> None:
        reformatted = json.loads(json.dumps(self.known_good, indent=7))
        self.assertEqual(profile_hash(self.known_good), profile_hash(reformatted))

    def test_reference_joint_order_is_semantic_and_12_dof(self) -> None:
        expected = [
            "FR_hip_joint", "FR_thigh_joint", "FR_calf_joint",
            "FL_hip_joint", "FL_thigh_joint", "FL_calf_joint",
            "RR_hip_joint", "RR_thigh_joint", "RR_calf_joint",
            "RL_hip_joint", "RL_thigh_joint", "RL_calf_joint",
        ]
        self.assertEqual(expected, [joint["name"] for joint in self.known_good["robot"]["joints"]])

    def test_eight_dof_profiles_are_rejected(self) -> None:
        profile = deepcopy(self.known_good)
        profile["robot"]["expected_joint_count"] = 8
        profile["robot"]["joints"] = profile["robot"]["joints"][:8]
        result = validate_profile(profile)
        self.assertTrue(any("exactly 12" in error for error in result["errors"]))

    def test_workspace_asset_uses_dedicated_onshape_root(self) -> None:
        profile = deepcopy(self.template)
        profile["robot"]["asset_usd"] = "/workspace/projects/training/assets/robot.usd"
        result = validate_profile(profile)
        self.assertTrue(any("assets/onshape" in error for error in result["errors"]))

    def test_checkpoint_must_match_robot_profile_namespace(self) -> None:
        profile = deepcopy(self.known_good)
        profile["training"]["checkpoint"] = (
            "/workspace/projects/training/logs/rl_games/"
            "quadruped_v2_custom_12dof_template/run/nn/policy.pth"
        )
        result = validate_profile(profile)
        self.assertTrue(any("same 12-DOF robot profile" in error for error in result["errors"]))

    def test_domain_randomization_ranges_are_ordered(self) -> None:
        profile = deepcopy(self.known_good)
        profile["domain_randomization"]["base_mass_scale_min"] = 1.5
        profile["domain_randomization"]["base_mass_scale_max"] = 0.5
        result = validate_profile(profile)
        self.assertTrue(any("base mass scale" in error for error in result["errors"]))

    def test_absolute_base_mass_variation_must_fit_target(self) -> None:
        profile = deepcopy(self.known_good)
        profile["domain_randomization"]["base_mass_target_kg"] = 0.65
        profile["domain_randomization"]["base_mass_variation_kg"] = 0.65
        result = validate_profile(profile)
        self.assertTrue(any("variation" in error for error in result["errors"]))

    def test_pyramid_stair_height_range_is_ordered(self) -> None:
        profile = deepcopy(self.known_good)
        profile["terrain"]["stairs_step_height_min"] = 0.03
        profile["terrain"]["stairs_step_height_max"] = 0.01
        result = validate_profile(profile)
        self.assertTrue(any("stairs_step_height" in error for error in result["errors"]))

    def test_actuator_randomization_ranges_are_ordered(self) -> None:
        profile = deepcopy(self.known_good)
        profile["domain_randomization"]["actuator_effort_scale_min"] = 1.1
        profile["domain_randomization"]["actuator_effort_scale_max"] = 0.9
        result = validate_profile(profile)
        self.assertTrue(any("actuator effort scale" in error for error in result["errors"]))

    def test_stationary_stance_action_matches_policy_contract(self) -> None:
        profile = deepcopy(self.known_good)
        profile["environment"]["stationary_stance_action"] = [0.0] * 11 + [1.2]
        result = validate_profile(profile)
        self.assertTrue(any("stationary_stance_action" in error for error in result["errors"]))

    def test_goal_stage_requires_full_mobility_command_ranges(self) -> None:
        profile = deepcopy(self.known_good)
        profile["training"]["stage"] = "V2Goal"
        profile["environment"]["surface"] = "Flat"
        profile["commands"]["forward_min"] = 0.0
        result = validate_profile(profile)
        self.assertTrue(any("fixed full-mobility" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
