"""Unit tests for deterministic autoresearch policy gates."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_HERE = Path(__file__).resolve().parent
_DEPLOYED_BIN = _HERE / "bin"
sys.path.insert(0, str(_DEPLOYED_BIN if _DEPLOYED_BIN.is_dir() else _HERE))

import robot_autoresearch as subject


class AutoresearchGateTests(unittest.TestCase):
    def test_rough_rollout_metrics_are_parsed_independently_of_training_logs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            console = Path(directory) / "console.log"
            console.write_text(
                "PLAY_METRICS step=50 command_forward=0.2500 "
                "body_forward=0.2000 body_lateral=-0.0200 "
                "forward_displacement=0.2000 heading_alignment=0.9900 "
                "swing_fraction_frflbrbl=0.4,0.5,0.4,0.5\n"
                "PLAY_METRICS step=100 command_forward=0.2500 "
                "body_forward=0.3000 body_lateral=0.0400 "
                "forward_displacement=0.5000 heading_alignment=1.0000 "
                "swing_fraction_frflbrbl=0.45,0.55,0.45,0.55\n",
                encoding="utf-8",
            )
            metrics = subject.parse_play_metrics(console)
            self.assertAlmostEqual(
                metrics["RoughRollout/mean_velocity_error"]["last"], 0.05
            )
            self.assertAlmostEqual(
                metrics["RoughRollout/mean_body_lateral_speed"]["last"], 0.03
            )
            self.assertEqual(
                metrics["RoughRollout/swing_fraction_front_left"]["last"],
                0.55,
            )

    def test_training_budget_counts_only_accumulated_ppo_time(self) -> None:
        supervisor = object.__new__(subject.Supervisor)
        supervisor.training_budget_seconds = 3600.0
        supervisor.state = {"training_seconds_completed": 3500.0}
        self.assertFalse(supervisor.training_budget_reached())
        with patch.object(subject.time, "monotonic", return_value=200.0):
            self.assertTrue(supervisor.training_budget_reached(active_since=50.0))

    def test_qwen_advisory_does_not_end_run(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            supervisor = object.__new__(subject.Supervisor)
            supervisor.run_dir = Path(directory)
            supervisor.run_id = "test-run"
            supervisor.manifest = {"robot_id": "unit-dog"}
            supervisor.state = {
                "cycle": 2,
                "checkpoint": "/workspace/projects/best.pth",
                "status": "running",
            }
            supervisor.write_advisory("model is uncertain", {"action": "escalate"})
            self.assertEqual(supervisor.state["status"], "running")
            self.assertTrue(
                (Path(directory) / "cycle-2-codex-advisory.md").is_file()
            )

    def test_extracts_json_from_reasoning_wrapper(self) -> None:
        decision = subject.extract_json(
            "analysis first\n```json\n"
            '{"action":"continue","tuning_changes":{}}\n```'
        )
        self.assertEqual(decision["action"], "continue")

    def test_limits_tuning_to_two_changes(self) -> None:
        tuning, notes = subject.bounded_tuning(
            {},
            {
                "body_vel_reward_scale": 5.5,
                "gait_reward_scale": 5.5,
                "foot_slip_penalty_scale": -2.2,
            },
        )
        self.assertEqual(len(tuning), 2)
        self.assertTrue(any("at most two" in note for note in notes))

    def test_rejects_large_tuning_step(self) -> None:
        tuning, notes = subject.bounded_tuning(
            {}, {"body_vel_reward_scale": 8.0}
        )
        self.assertEqual(tuning, {})
        self.assertTrue(any("25%" in note for note in notes))

    def test_promotion_score_penalizes_falls(self) -> None:
        safe = {
            "rewards/iter": {"last": 100.0},
            "Episode/Metrics/mean_survival_fraction": {"last": 1.0},
            "Episode/Episode_Termination/fell": {"last": 0.0},
            "Episode/Metrics/mean_velocity_error": {"last": 0.1},
            "Episode/Metrics/mean_body_lateral_speed": {"last": 0.02},
            "Episode/Metrics/mean_heading_error": {"last": 0.01},
        }
        falling = json.loads(json.dumps(safe))
        falling["Episode/Episode_Termination/fell"]["last"] = 1.0
        self.assertGreater(
            subject.promotion_score(safe), subject.promotion_score(falling)
        )

    def test_promotion_score_rejects_stationary_survival_loophole(self) -> None:
        walking = {
            "Episode/Metrics/mean_survival_fraction": {"last": 1.0},
            "Episode/Episode_Termination/fell": {"last": 0.0},
            "Episode/Metrics/mean_velocity_error": {"last": 0.08},
            "Episode/Metrics/mean_body_lateral_speed": {"last": 0.02},
            "Episode/Metrics/mean_heading_error": {"last": 0.01},
        }
        stationary = json.loads(json.dumps(walking))
        stationary["Episode/Metrics/mean_velocity_error"]["last"] = 0.24
        self.assertGreater(
            subject.promotion_score(walking),
            subject.promotion_score(stationary),
        )

    def test_meaningful_promotion_requires_net_progress_and_all_four_feet(self) -> None:
        incumbent = {
            "RoughRollout/mean_survival_fraction": {"last": 1.0},
            "RoughRollout/Episode_Termination/fell": {"last": 0.0},
            "RoughRollout/mean_velocity_error": {"last": 0.24},
            "RoughRollout/mean_body_lateral_speed": {"last": 0.08},
            "RoughRollout/mean_heading_error": {"last": 0.02},
            "RoughRollout/forward_displacement": {"last": 0.0},
        }
        candidate = {
            "RoughRollout/mean_survival_fraction": {"last": 1.0},
            "RoughRollout/Episode_Termination/fell": {"last": 0.0},
            "RoughRollout/mean_velocity_error": {"last": 0.10},
            "RoughRollout/mean_body_lateral_speed": {"last": 0.03},
            "RoughRollout/mean_heading_error": {"last": 0.01},
            "RoughRollout/forward_displacement": {"last": 0.55},
            "RoughRollout/swing_fraction_front_right": {"last": 0.45},
            "RoughRollout/swing_fraction_front_left": {"last": 0.50},
            "RoughRollout/swing_fraction_back_right": {"last": 0.45},
            "RoughRollout/swing_fraction_back_left": {"last": 0.50},
        }
        self.assertTrue(subject.is_meaningful_promotion(candidate, incumbent))
        candidate["RoughRollout/forward_displacement"]["last"] = 0.08
        self.assertFalse(subject.is_meaningful_promotion(candidate, incumbent))
        candidate["RoughRollout/forward_displacement"]["last"] = 0.55
        candidate["RoughRollout/swing_fraction_back_right"]["last"] = 0.0
        self.assertFalse(subject.is_meaningful_promotion(candidate, incumbent))

    def test_latest_training_checkpoint_prefers_bounded_epoch_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            nn_dir = Path(directory) / "nn"
            nn_dir.mkdir()
            preferred = nn_dir / "simple_dog_rough_velocity_direct.pth"
            bounded = nn_dir / "last_simple_dog_rough_velocity_direct_ep_653.pth"
            preferred.write_bytes(b"best")
            bounded.write_bytes(b"latest")
            self.assertEqual(
                subject.Supervisor.latest_training_checkpoint(
                    Path(directory), preferred.name
                ),
                bounded,
            )

    def test_terminal_training_reward_is_parsed_from_rl_games_checkpoint(self) -> None:
        checkpoint = Path(
            "last_simple_dog_rough_velocity_direct_ep_20_rew__-12.134598_.pth"
        )
        self.assertEqual(subject.checkpoint_training_reward(checkpoint), -12.134598)
        self.assertIsNone(
            subject.checkpoint_training_reward(Path("prepared_after_target_20.pth"))
        )

    def test_intermediate_candidates_are_ranked_ahead_of_regressed_final(self) -> None:
        candidates = [
            {"chunk": 8, "training_reward": -12.20},
            {"chunk": 9, "training_reward": -12.13},
            {"chunk": 10, "training_reward": -13.37},
            {"chunk": 7, "training_reward": None},
        ]
        ranked = subject.rank_training_candidates(candidates, 3)
        self.assertEqual([item["chunk"] for item in ranked], [9, 8, 10])

    def test_training_chunk_retries_stalled_bootstrap_without_charging_it(self) -> None:
        supervisor = object.__new__(subject.Supervisor)
        supervisor.state = {}
        supervisor.start_training = MagicMock(
            side_effect=[Path("/tmp/attempt-1"), Path("/tmp/attempt-2")]
        )
        supervisor.wait_training = MagicMock(
            side_effect=[
                subject.SupervisorError(
                    "Training made no console progress for 120 seconds."
                ),
                None,
            ]
        )
        supervisor.update = MagicMock()
        supervisor.stop_owned = MagicMock()
        supervisor.start_lab = MagicMock()
        supervisor.completed_training_seconds = MagicMock(return_value=30.0)
        run_dir, elapsed = supervisor.run_training_chunk(
            "/workspace/projects/source.pth",
            42,
            Path("/tmp/tuning.json"),
        )
        self.assertEqual(run_dir, Path("/tmp/attempt-2"))
        self.assertEqual(elapsed, 30.0)
        self.assertEqual(supervisor.stop_owned.call_count, 1)
        self.assertEqual(supervisor.start_lab.call_count, 1)
        self.assertEqual(supervisor.start_training.call_count, 2)

    def test_training_chunk_does_not_retry_real_training_failure(self) -> None:
        supervisor = object.__new__(subject.Supervisor)
        supervisor.state = {}
        supervisor.start_training = MagicMock(return_value=Path("/tmp/attempt-1"))
        supervisor.wait_training = MagicMock(
            side_effect=subject.SupervisorError(
                "Training ended with status failed."
            )
        )
        supervisor.update = MagicMock()
        supervisor.stop_owned = MagicMock()
        supervisor.start_lab = MagicMock()
        with self.assertRaisesRegex(subject.SupervisorError, "status failed"):
            supervisor.run_training_chunk(
                "/workspace/projects/source.pth",
                42,
                Path("/tmp/tuning.json"),
            )
        supervisor.stop_owned.assert_not_called()
        supervisor.start_lab.assert_not_called()

    def test_rollout_retries_stalled_bootstrap(self) -> None:
        supervisor = object.__new__(subject.Supervisor)
        supervisor.state = {}
        supervisor.update = MagicMock()
        supervisor.start_lab = MagicMock()
        supervisor.run_rollout_attempt = MagicMock(
            side_effect=[
                subject.SupervisorError(
                    "Rollout made no console progress for 120 seconds."
                ),
                None,
            ]
        )
        checkpoint = Path("/tmp/checkpoint.pth")
        experiment = Path("/tmp/experiment")
        supervisor.run_rollout_with_retries(checkpoint, experiment)
        self.assertEqual(supervisor.run_rollout_attempt.call_count, 2)
        self.assertEqual(supervisor.start_lab.call_count, 1)

    def test_rollout_does_not_retry_invalid_checkpoint(self) -> None:
        supervisor = object.__new__(subject.Supervisor)
        supervisor.state = {}
        supervisor.update = MagicMock()
        supervisor.start_lab = MagicMock()
        supervisor.run_rollout_attempt = MagicMock(
            side_effect=subject.SupervisorError(
                "Command failed (docker): invalid checkpoint"
            )
        )
        with self.assertRaisesRegex(subject.SupervisorError, "invalid checkpoint"):
            supervisor.run_rollout_with_retries(
                Path("/tmp/checkpoint.pth"),
                Path("/tmp/experiment"),
            )
        supervisor.start_lab.assert_not_called()

    def test_dry_run_refuses_external_playback(self) -> None:
        manifest = {
            "schema_version": 1,
            "robot_id": "unit-dog",
            "adapter": "simple_dog_v1",
            "checkpoint": (
                "/workspace/projects/training/logs/rl_games/"
                "simple_dog_velocity_direct/test/nn/"
                "simple_dog_velocity_direct.pth"
            ),
            "num_envs": 128,
            "next_max_iterations": 10,
            "iteration_increment": 10,
            "max_cycles": 1,
            "poll_seconds": 5,
            "video_length": 100,
            "qwen_model": "test-model",
            "terrain": "rough",
            "max_minutes": 30,
            "tuning": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            supervisor = subject.Supervisor(path, dry_run=True)
            with patch.object(
                subject,
                "container_running",
                side_effect=lambda name: name == subject.PLAYBACK_CONTAINER,
            ):
                with self.assertRaisesRegex(
                    subject.SupervisorError, "isaac-lab-dog-stream"
                ):
                    supervisor.require_free_gpu()

    def test_dry_run_refuses_external_lab(self) -> None:
        with patch.object(
            subject,
            "container_running",
            side_effect=lambda name: name == subject.LAB_CONTAINER,
        ):
            supervisor = object.__new__(subject.Supervisor)
            with self.assertRaisesRegex(subject.SupervisorError, "isaac-lab-gb10"):
                supervisor.require_free_gpu()

    def test_completed_training_seconds_uses_reported_ppo_time(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "console.log").write_text(
                "startup overhead\nTraining time: 191.55 seconds\n",
                encoding="utf-8",
            )
            self.assertEqual(
                subject.Supervisor.completed_training_seconds(run_dir),
                191.55,
            )


if __name__ == "__main__":
    unittest.main()
