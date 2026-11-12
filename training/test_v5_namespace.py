import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).parent
PACKAGE = ROOT / "simple_dog_task_current_body_v5"


class CurrentBodyV5NamespaceTests(unittest.TestCase):
    def test_v5_task_ids_and_experiment_namespace_are_distinct(self):
        registration = (PACKAGE / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("CurrentBodyV5-Hard", registration)
        self.assertIn("CurrentBodyV5-Simple-Dog-Direct-Eval", registration)
        self.assertIn("CurrentBodyV5-Simple-Dog-Direct-Play", registration)
        self.assertNotIn('"Isaac-Locomotion-CurrentBodyV4', registration)

        agent = (PACKAGE / "agents" / "rl_games_ppo_cfg.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("name: simple_dog_current_body_v5_hard_direct", agent)

    def test_v5_explicitly_disables_all_inherited_gait_rewards(self):
        module = ast.parse(
            (PACKAGE / "simple_dog_current_body_v5_env_cfg.py").read_text(
                encoding="utf-8"
            )
        )
        hard_config = next(
            node
            for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == "SimpleDogCurrentBodyV5HardEnvCfg"
        )
        assignments = {
            target.id: statement.value
            for statement in hard_config.body
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
        }
        self.assertEqual(ast.literal_eval(assignments["policy_family"]), "current_body_v5")
        for field in (
            "gait_reward_scale",
            "diagonal_gait_reward_scale",
            "complete_gait_cycle_reward_scale",
            "reference_trot_reward_scale",
            "clocked_trot_reward_scale",
            "diagonal_joint_symmetry_reward_scale",
        ):
            self.assertEqual(ast.literal_eval(assignments[field]), 0.0)

    def test_scratch_queue_launches_v5_without_a_checkpoint(self):
        queue = (ROOT / "run_simple_dog_scratch_seed_queue.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("'' '' currentbodyv5hard", queue)
        self.assertNotIn("'' '' currentbodyv4hard", queue)


if __name__ == "__main__":
    unittest.main()
