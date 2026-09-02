import ast
from pathlib import Path
import unittest

from v4_difficulty_ramp import difficulty_fraction, scheduled_terrain_level


class V4DifficultyRampTests(unittest.TestCase):
    def test_difficulty_is_present_initially_and_reaches_full_on_schedule(self):
        self.assertAlmostEqual(difficulty_fraction(0, 9_600, 0.15), 0.15)
        self.assertAlmostEqual(difficulty_fraction(4_800, 9_600, 0.15), 0.575)
        self.assertAlmostEqual(difficulty_fraction(9_600, 9_600, 0.15), 1.0)
        self.assertAlmostEqual(difficulty_fraction(20_000, 9_600, 0.15), 1.0)

    def test_terrain_reaches_the_hardest_row_at_the_fixed_step(self):
        self.assertEqual(scheduled_terrain_level(0, 9_600, 6, 0.15), 0)
        self.assertEqual(scheduled_terrain_level(4_800, 9_600, 6, 0.15), 3)
        self.assertEqual(scheduled_terrain_level(9_600, 9_600, 6, 0.15), 5)

    def test_ramp_does_not_enter_the_reward_function(self):
        source_path = Path(__file__).parent / "simple_dog_task_current_body_v4" / "simple_dog_current_body_v4_env.py"
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        reward = next(
            node
            for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_get_rewards"
        )
        reward_source = ast.unparse(reward)
        self.assertNotIn("difficulty_fraction", reward_source)
        self.assertNotIn("scheduled_terrain_level", reward_source)

    def test_v4_has_no_opposite_leg_pairing_reward_or_state(self):
        package = Path(__file__).parent / "simple_dog_task_current_body_v4"
        env_source = (package / "simple_dog_current_body_v4_env.py").read_text(
            encoding="utf-8"
        )
        module = ast.parse(env_source)
        reward = next(
            node
            for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_get_rewards"
        )
        reward_source = ast.unparse(reward)
        for forbidden in (
            "diagonal",
            "pair",
            "trot",
            "phase",
            "gait",
            "super()._get_rewards",
        ):
            self.assertNotIn(forbidden, reward_source)
        for inherited_state in (
            "_gait_landing_counts",
            "_steps_since_complete_gait_cycle",
            "_foot_swing_duty_ema",
        ):
            self.assertNotIn(inherited_state, env_source)

        config_module = ast.parse(
            (package / "simple_dog_current_body_v4_env_cfg.py").read_text(
                encoding="utf-8"
            )
        )
        hard_config = next(
            node
            for node in config_module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "SimpleDogCurrentBodyV4HardEnvCfg"
        )
        assignments = {
            target.id: statement.value
            for statement in hard_config.body
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
        }
        for field in (
            "gait_reward_scale",
            "diagonal_gait_reward_scale",
            "complete_gait_cycle_reward_scale",
            "reference_trot_reward_scale",
            "clocked_trot_reward_scale",
            "diagonal_joint_symmetry_reward_scale",
        ):
            self.assertEqual(ast.literal_eval(assignments[field]), 0.0)

    def test_locked_drop_is_not_ended_by_fall_detection(self):
        source_path = (
            Path(__file__).parent
            / "simple_dog_task_current"
            / "simple_dog_current_env.py"
        )
        module = ast.parse(source_path.read_text(encoding="utf-8"))
        get_dones = next(
            node
            for node in ast.walk(module)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_get_dones"
        )
        get_dones_source = ast.unparse(get_dones)
        self.assertIn("terminated & ~self._reset_hold_active_mask", get_dones_source)


if __name__ == "__main__":
    unittest.main()
