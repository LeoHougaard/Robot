import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).parent


class CurrentBodyV6V19NamespaceTests(unittest.TestCase):
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
        config_nodes = list(hard_config.body)
        if version == 17:
            locomotion_config = next(
                node for node in module.body
                if isinstance(node, ast.ClassDef)
                and node.name == "_V17LocomotionObjective"
            )
            config_nodes = list(locomotion_config.body) + config_nodes
        return {
            target.id: ast.literal_eval(statement.value)
            for statement in config_nodes
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
        }

    def test_distinct_task_and_experiment_namespaces(self):
        for version in (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19):
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

        for version in (6, 7, 8, 9, 10, 11, 12, 13, 14, 15):
            package = ROOT / f"simple_dog_task_current_body_v{version}"
            env_source = (
                package / f"simple_dog_current_body_v{version}_env.py"
            ).read_text(encoding="utf-8")
            cfg_source = (
                package / f"simple_dog_current_body_v{version}_env_cfg.py"
            ).read_text(encoding="utf-8")
            self.assertNotIn("def _get_rewards", env_source)
            self.assertNotIn("reward_scale =", cfg_source)
            expected_parent = (
                "SimpleDogCurrentBodyV5" if version < 8
                else "SimpleDogCurrentBodyV10" if version == 11
                else "SimpleDogCurrentBodyV11" if version == 12
                else "SimpleDogCurrentBodyV12" if version == 13
                else "SimpleDogCurrentBodyV13" if version == 14
                else "SimpleDogCurrentBodyV14" if version == 15
                else "SimpleDogCurrentBodyV15" if version == 16
                else "SimpleDogCurrentBodyV7"
            )
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
        for version in (6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19):
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
        self.assertIn('/isaac-sim/python.sh - "$checkpoint"', playback)
        self.assertNotIn(
            '/workspace/isaaclab/_isaac_sim/kit/python/bin/python3 - "$checkpoint"',
            playback,
        )
        self.assertNotIn(
            'current-body-v4-${simulation_fit_sha:0:12}.json', backend
        )
        windows_launcher = (ROOT.parent / "Start-SimpleDogTraining.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "$isV17Terrain -or $isV18Terrain -or $isV19Terrain",
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

    def test_v11_stages_terrain_and_dynamics_without_reward_changes(self):
        package = ROOT / "simple_dog_task_current_body_v11"
        env_source = (package / "simple_dog_current_body_v11_env.py").read_text(
            encoding="utf-8"
        )
        cfg_source = (package / "simple_dog_current_body_v11_env_cfg.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("SimpleDogCurrentBodyV10Env", env_source)
        self.assertIn("max_init_terrain_level = 0", cfg_source)
        self.assertIn("difficulty_ramp_floor = 0.01", cfg_source)
        self.assertIn("difficulty_ramp_terrain_band_rows = 1", cfg_source)
        self.assertIn("difficulty_ramp_full_step = 32 * 200", cfg_source)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertNotIn("reward_scale =", cfg_source)

    def test_v12_adds_action_exploration_without_reward_changes(self):
        package = ROOT / "simple_dog_task_current_body_v12"
        env_source = (package / "simple_dog_current_body_v12_env.py").read_text(
            encoding="utf-8"
        )
        cfg_source = (package / "simple_dog_current_body_v12_env_cfg.py").read_text(
            encoding="utf-8"
        )
        agent = (package / "agents" / "rl_games_ppo_cfg.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("SimpleDogCurrentBodyV11Env", env_source)
        self.assertIn("sigma_init: {name: const_initializer, val: 0.0}", agent)
        self.assertIn("entropy_coef: 0.02", agent)
        self.assertIn("save_frequency: 10", agent)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertNotIn("reward_scale =", cfg_source)

    def test_v13_adds_long_credit_and_early_physical_pushes(self):
        cfg = self._input_assignments(13)
        package = ROOT / "simple_dog_task_current_body_v13"
        env_source = (package / "simple_dog_current_body_v13_env.py").read_text(
            encoding="utf-8"
        )
        cfg_source = (package / "simple_dog_current_body_v13_env_cfg.py").read_text(
            encoding="utf-8"
        )
        agent = (package / "agents" / "rl_games_ppo_cfg.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("SimpleDogCurrentBodyV12Env", env_source)
        self.assertEqual(cfg["isolated_motion_fraction"], 1.0)
        self.assertEqual(cfg["isolated_linear_axis_fraction"], 1.0)
        self.assertGreaterEqual(cfg["push_difficulty_floor"], 0.50)
        self.assertIn("horizon_length: 128", agent)
        self.assertIn("save_frequency: 2", agent)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertNotIn("reward_scale =", cfg_source)

    def test_v14_compacts_deployable_history_without_reward_changes(self):
        package = ROOT / "simple_dog_task_current_body_v14"
        env_source = (package / "simple_dog_current_body_v14_env.py").read_text(
            encoding="utf-8"
        )
        cfg_source = (package / "simple_dog_current_body_v14_env_cfg.py").read_text(
            encoding="utf-8"
        )
        agent = (package / "agents" / "rl_games_ppo_cfg.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("SimpleDogCurrentBodyV13Env", env_source)
        self.assertIn("V14_SELECTED_HISTORY_INDICES = (0, 5, 10, 15, 20, 23)", cfg_source)
        self.assertIn("V14_OBSERVATION_SPACE", cfg_source)
        self.assertIn("horizon_length: 128", agent)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertNotIn("reward_scale =", cfg_source)

    def test_v15_delays_physical_pushes_without_reward_changes(self):
        package = ROOT / "simple_dog_task_current_body_v15"
        env_source = (package / "simple_dog_current_body_v15_env.py").read_text(
            encoding="utf-8"
        )
        cfg_source = (package / "simple_dog_current_body_v15_env_cfg.py").read_text(
            encoding="utf-8"
        )
        cfg = self._input_assignments(15)
        self.assertIn("SimpleDogCurrentBodyV14Env", env_source)
        self.assertEqual(cfg["push_difficulty_floor"], 0.0)
        self.assertNotIn("push_probability =", cfg_source)
        self.assertNotIn("push_force_n =", cfg_source)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertNotIn("reward_scale =", cfg_source)

    def test_v16_strengthens_only_the_no_motion_penalty(self):
        package = ROOT / "simple_dog_task_current_body_v16"
        env_source = (package / "simple_dog_current_body_v16_env.py").read_text(
            encoding="utf-8"
        )
        cfg_source = (package / "simple_dog_current_body_v16_env_cfg.py").read_text(
            encoding="utf-8"
        )
        cfg = self._input_assignments(16)
        self.assertIn("SimpleDogCurrentBodyV15Env", env_source)
        self.assertEqual(cfg["body_motion_shortfall_penalty_scale"], -3.0)
        self.assertNotIn("push_probability =", cfg_source)
        self.assertNotIn("push_force_n =", cfg_source)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertNotIn("gait_reward_scale =", cfg_source)
        self.assertNotIn("diagonal_gait_reward_scale =", cfg_source)

    def test_v17_is_a_locomotion_only_scratch_objective(self):
        package = ROOT / "simple_dog_task_current_body_v17"
        env_source = (package / "simple_dog_current_body_v17_env.py").read_text(
            encoding="utf-8"
        )
        cfg = self._input_assignments(17)
        agent_source = (package / "agents" / "rl_games_ppo_cfg.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("SimpleDogCurrentBodyV16Env", env_source)
        self.assertIn("def _sample_command_targets", env_source)
        self.assertIn("def _sample_posture_targets", env_source)
        self.assertIn("def _get_rewards", env_source)
        self.assertEqual(cfg["locomotion_speed_range"], (0.10, 0.20))
        self.assertEqual(cfg["episode_length_s"], 90.0)
        self.assertEqual(cfg["locomotion_shortfall_penalty_scale"], -6.0)
        self.assertEqual(cfg["locomotion_level_penalty_scale"], -2.0)
        self.assertIn("horizon_length: 64", agent_source)
        self.assertNotIn("diagonal", env_source.lower())

    def test_v18_adds_only_subordinate_progress_gated_opposite_leg_sync(self):
        package = ROOT / "simple_dog_task_current_body_v18"
        env_source = (package / "simple_dog_current_body_v18_env.py").read_text(
            encoding="utf-8"
        )
        cfg = self._input_assignments(18)
        self.assertIn("SimpleDogCurrentBodyV17Env", env_source)
        self.assertIn("self._dense_diagonal_gait_reward", env_source)
        self.assertIn("* progress", env_source)
        self.assertEqual(cfg["opposite_leg_sync_reward_scale"], 0.25)
        self.assertLess(
            cfg["opposite_leg_sync_reward_scale"],
            2.0,
        )

    def test_v19_is_the_stiff_scratch_alternative_without_reward_changes(self):
        package = ROOT / "simple_dog_task_current_body_v19"
        env_source = (package / "simple_dog_current_body_v19_env.py").read_text(
            encoding="utf-8"
        )
        cfg = self._input_assignments(19)
        agent_source = (package / "agents" / "rl_games_ppo_cfg.yaml").read_text(
            encoding="utf-8"
        )
        self.assertIn("SimpleDogCurrentBodyV18Env", env_source)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertEqual(cfg["actuator_drive_scale"], (1.0, 1.35))
        self.assertIn("load_checkpoint: false", agent_source)
        self.assertIn("horizon_length: 64", agent_source)

    def test_v7_uses_post_settle_physical_pushes_without_reward_changes(self):
        cfg = self._input_assignments(7)
        self.assertGreaterEqual(cfg["push_interval_s"][0], 6.0)
        self.assertGreater(cfg["push_force_n"][0], 0.0)
        self.assertGreater(cfg["push_force_n"][1], cfg["push_force_n"][0])
        self.assertGreater(cfg["push_force_duration_s"][0], 0.0)
        self.assertEqual(cfg["push_difficulty_floor"], 0.0)

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
        self.assertIn("self.cfg.push_difficulty_floor", env_source)
        self.assertNotIn("write_root_velocity", env_source)
        self.assertNotIn("def _get_rewards", env_source)
        self.assertIn("CurrentBodyV7-Simple-Dog-Direct-Push-Eval", registration)

        cfg_module = ast.parse(
            (package / "simple_dog_current_body_v7_env_cfg.py").read_text(
                encoding="utf-8"
            )
        )
        for class_name in (
            "SimpleDogCurrentBodyV7EvalEnvCfg",
            "SimpleDogCurrentBodyV7PlayEnvCfg",
        ):
            config_class = next(
                node for node in cfg_module.body
                if isinstance(node, ast.ClassDef) and node.name == class_name
            )
            assignments = {
                target.id: ast.literal_eval(statement.value)
                for statement in config_class.body
                if isinstance(statement, ast.Assign)
                for target in statement.targets
                if isinstance(target, ast.Name)
            }
            self.assertEqual(assignments["push_difficulty_floor"], 0.0)


if __name__ == "__main__":
    unittest.main()
