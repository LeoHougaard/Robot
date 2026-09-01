import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).parent


class CurrentBodyV6V10NamespaceTests(unittest.TestCase):
    def _input_assignments(self, version: int):
        package = ROOT / f"simple_dog_task_current_body_v{version}"
        module = ast.parse(
            (package / f"simple_dog_current_body_v{version}_env_cfg.py").read_text(
                encoding="utf-8"
            )
        )
        hard_config = next(
            node for node in module.body
            if isinstance(node, ast.ClassDef)
            and node.name == f"SimpleDogCurrentBodyV{version}HardEnvCfg"
        )
        return {
            target.id: ast.literal_eval(statement.value)
            for statement in hard_config.body
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
        }

    def test_distinct_task_and_experiment_namespaces(self):
        for version in (6, 7, 8, 9, 10):
            package = ROOT / f"simple_dog_task_current_body_v{version}"
            registration = (package / "__init__.py").read_text(encoding="utf-8")
            agent = (package / "agents" / "rl_games_ppo_cfg.yaml").read_text(
                encoding="utf-8"
            )
            self.assertIn(f"CurrentBodyV{version}-Hard", registration)
            self.assertIn(f"simple_dog_current_body_v{version}_hard_direct", agent)

    def test_translation_priority_changes_inputs_only(self):
        v6 = self._input_assignments(6)
        v7 = self._input_assignments(7)
        self.assertAlmostEqual(
            sum(v6[name] for name in (
                "mixed_command_fraction", "posture_only_fraction",
                "isolated_motion_fraction", "neutral_fraction",
            )), 1.0
        )
        self.assertAlmostEqual(
            sum(v7[name] for name in (
                "mixed_command_fraction", "posture_only_fraction",
                "isolated_motion_fraction", "neutral_fraction",
            )), 1.0
        )
        self.assertGreater(v6["mixed_command_fraction"], 0.75)
        self.assertGreater(v7["mixed_command_fraction"], v6["mixed_command_fraction"])
        self.assertGreaterEqual(v6["linear_command_hold_s"][0], 6.0)
        self.assertGreater(v7["linear_command_hold_s"][0], v6["linear_command_hold_s"][0])

        for version in (6, 7, 8, 9, 10):
            package = ROOT / f"simple_dog_task_current_body_v{version}"
            env_source = (
                package / f"simple_dog_current_body_v{version}_env.py"
            ).read_text(encoding="utf-8")
            cfg_source = (
                package / f"simple_dog_current_body_v{version}_env_cfg.py"
            ).read_text(encoding="utf-8")
            self.assertNotIn("def _get_rewards", env_source)
            self.assertNotIn("reward_scale =", cfg_source)
            expected_parent = "SimpleDogCurrentBodyV5" if version < 8 else "SimpleDogCurrentBodyV7"
            self.assertIn(expected_parent, cfg_source)

    def test_current_body_reward_keeps_episode_motion_telemetry(self):
        source = (
            ROOT
            / "simple_dog_task_current_body_v4"
            / "simple_dog_current_body_v4_env.py"
        ).read_text(encoding="utf-8")
        self.assertIn("self._survival_steps += 1.0", source)
        self.assertIn("self._velocity_error_sum += active", source)
        self.assertIn("self._world_forward_speed_sum += active * body_forward", source)
        self.assertIn(
            "self._body_lateral_speed_sum += active * torch.abs(body_lateral)",
            source,
        )
        self.assertIn("self._heading_error_sum += active", source)
        self.assertIn("self._terrain_commanded_distance +=", source)
        self.assertIn("active * commanded_speed * self.step_dt", source)
        self.assertIn("self._terrain_tracked_distance += active", source)

        base_source = (
            ROOT / "simple_dog_task" / "simple_dog_env.py"
        ).read_text(encoding="utf-8")
        self.assertIn('log["Metrics/commanded_distance"]', base_source)
        self.assertIn('log["Metrics/tracked_distance"]', base_source)
        self.assertIn('log["Metrics/command_tracking_fraction"]', base_source)

    def test_launch_and_playback_support_all_scratch_variants(self):
        launcher = (ROOT / "run_simple_dog.sh").read_text(encoding="utf-8")
        playback = (ROOT / "render_simple_dog_playback.sh").read_text(
            encoding="utf-8"
        )
        backend = (ROOT / "simple-dog-gb10.sh").read_text(encoding="utf-8")
        for version in (6, 7, 8, 9, 10):
            terrain = f"currentbodyv{version}hard"
            family = f"current_body_v{version}"
            self.assertIn(terrain, launcher)
            self.assertIn(f'"$terrain" == currentbodyv{version}*', launcher)
            self.assertIn(family, launcher)
            self.assertIn(terrain, playback)
            self.assertIn(family, playback)
            playback_branch = playback.split(f"  {terrain})", 1)[1].split(
                "    ;;", 1
            )[0]
            self.assertIn('-f "$simulation_fit"', playback_branch)
            self.assertIn(
                'export SIMPLE_DOG_SIMULATION_FIT="$simulation_fit"',
                playback_branch,
            )
            self.assertIn("SIMPLE_DOG_REVIEW_NUM_ENVS:-5", playback_branch)

        self.assertIn(
            'current-body-*-${simulation_fit_sha:0:12}.json', backend
        )
        self.assertNotIn(
            'current-body-v4-${simulation_fit_sha:0:12}.json', backend
        )
        windows_launcher = (ROOT.parent / "Start-SimpleDogTraining.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "$isV9Terrain -or $isV10Terrain",
            windows_launcher,
        )
        self.assertIn('"current-body-v5"', windows_launcher)

    def test_v8_changes_only_the_input_distribution(self):
        v8 = self._input_assignments(8)
        self.assertAlmostEqual(
            sum(v8[name] for name in (
                "mixed_command_fraction", "posture_only_fraction",
                "isolated_motion_fraction", "neutral_fraction",
            )), 1.0
        )
        self.assertGreaterEqual(v8["mixed_command_fraction"], 0.50)
        self.assertGreater(v8["isolated_motion_fraction"], 0.30)
        self.assertGreater(v8["isolated_linear_axis_fraction"], 0.95)
        self.assertGreaterEqual(v8["linear_command_hold_s"][0], 10.0)
        package = ROOT / "simple_dog_task_current_body_v8"
        env_source = (package / "simple_dog_current_body_v8_env.py").read_text(
            encoding="utf-8"
        )
        cfg_source = (package / "simple_dog_current_body_v8_env_cfg.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("def _get_rewards", env_source)
        self.assertNotIn("reward_scale =", cfg_source)
        self.assertIn("SimpleDogCurrentBodyV7Env", env_source)

    def test_v9_is_a_distinct_ppo_exploration_hypothesis(self):
        package = ROOT / "simple_dog_task_current_body_v9"
        env_source = (package / "simple_dog_current_body_v9_env.py").read_text(
            encoding="utf-8"
        )
        cfg_source = (package / "simple_dog_current_body_v9_env_cfg.py").read_text(
            encoding="utf-8"
        )
        agent = (package / "agents" / "rl_games_ppo_cfg.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("SimpleDogCurrentBodyV7Env", env_source)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertNotIn("reward_scale =", cfg_source)
        self.assertIn("separate: true", agent)
        self.assertIn("sigma_init: {name: const_initializer, val: -0.1}", agent)
        self.assertIn("entropy_coef: 0.015", agent)
        self.assertIn("horizon_length: 64", agent)
        self.assertIn("mlp: {units: [512, 256, 128]", agent)

    def test_v10_bootstraps_translation_through_inputs_only(self):
        v10 = self._input_assignments(10)
        self.assertAlmostEqual(
            sum(v10[name] for name in (
                "mixed_command_fraction", "posture_only_fraction",
                "isolated_motion_fraction", "neutral_fraction",
            )), 1.0
        )
        self.assertGreaterEqual(v10["isolated_motion_fraction"], 0.90)
        self.assertGreaterEqual(v10["isolated_linear_axis_fraction"], 0.99)
        self.assertGreaterEqual(v10["isolated_forward_share"], 0.85)
        self.assertGreaterEqual(v10["linear_command_hold_s"][0], 12.0)
        package = ROOT / "simple_dog_task_current_body_v10"
        env_source = (package / "simple_dog_current_body_v10_env.py").read_text(
            encoding="utf-8"
        )
        cfg_source = (package / "simple_dog_current_body_v10_env_cfg.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("SimpleDogCurrentBodyV7Env", env_source)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertNotIn("reward_scale =", cfg_source)

    def test_v7_uses_post_settle_physical_pushes_without_reward_changes(self):
        cfg = self._input_assignments(7)
        self.assertGreaterEqual(cfg["push_interval_s"][0], 6.0)
        self.assertGreater(cfg["push_force_n"][0], 0.0)
        self.assertGreater(cfg["push_force_n"][1], cfg["push_force_n"][0])
        self.assertGreater(cfg["push_force_duration_s"][0], 0.0)

        package = ROOT / "simple_dog_task_current_body_v7"
        env_source = (package / "simple_dog_current_body_v7_env.py").read_text(
            encoding="utf-8"
        )
        registration = (package / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("_reset_settle_steps_remaining > 0", env_source)
        self.assertIn(
            "permanent_wrench_composer.set_forces_and_torques_index",
            env_source,
        )
        self.assertNotIn("write_root_velocity", env_source)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertIn("CurrentBodyV7-Simple-Dog-Direct-Push-Eval", registration)


if __name__ == "__main__":
    unittest.main()
