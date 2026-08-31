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
        self.assertEqual(scheduled_terrain_level(0, 9_600, 12, 0.15), 1)
        self.assertEqual(scheduled_terrain_level(4_800, 9_600, 12, 0.15), 6)
        self.assertEqual(scheduled_terrain_level(9_600, 9_600, 12, 0.15), 11)

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


if __name__ == "__main__":
    unittest.main()
