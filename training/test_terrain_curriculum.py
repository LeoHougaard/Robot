"""Tests for rough-terrain curriculum progression."""

import unittest

from terrain_curriculum import (
    base_contact_is_terminal,
    classify_terrain_progress,
    staged_command_thresholds,
)


class TerrainCurriculumTests(unittest.TestCase):
    def test_staged_command_thresholds_leave_forward_rehearsal(self):
        thresholds = staged_command_thresholds(
            turn_fraction=0.15,
            stand_fraction=0.10,
            reverse_fraction=0.12,
            lateral_fraction=0.18,
            diagonal_fraction=0.25,
        )
        self.assertEqual(thresholds, (0.15, 0.25, 0.37, 0.55, 0.8))

    def test_staged_command_thresholds_reject_overallocation(self):
        with self.assertRaises(ValueError):
            staged_command_thresholds(
                turn_fraction=0.25,
                stand_fraction=0.20,
                reverse_fraction=0.25,
                lateral_fraction=0.20,
                diagonal_fraction=0.20,
            )

    def test_fall_reward_uses_explicit_base_contact_suppression(self):
        self.assertFalse(
            base_contact_is_terminal(suppress_base_contact_termination=True)
        )
        self.assertTrue(
            base_contact_is_terminal(suppress_base_contact_termination=False)
        )

    def test_promotes_good_tracking_after_enough_commanded_distance(self):
        move_up, move_down = classify_terrain_progress(
            completed_steps=500,
            max_episode_length=600,
            commanded_distance=1.2,
            tracked_distance=0.9,
            terrain_size=4.0,
        )
        self.assertTrue(move_up)
        self.assertFalse(move_down)

    def test_demotes_poor_tracking(self):
        move_up, move_down = classify_terrain_progress(
            completed_steps=500,
            max_episode_length=600,
            commanded_distance=1.2,
            tracked_distance=0.4,
            terrain_size=4.0,
        )
        self.assertFalse(move_up)
        self.assertTrue(move_down)

    def test_short_or_mostly_stationary_episode_does_not_change_level(self):
        cases = (
            (100, 1.2, 1.0),
            (500, 0.3, 0.3),
        )
        for completed_steps, commanded_distance, tracked_distance in cases:
            with self.subTest(completed_steps=completed_steps):
                move_up, move_down = classify_terrain_progress(
                    completed_steps=completed_steps,
                    max_episode_length=600,
                    commanded_distance=commanded_distance,
                    tracked_distance=tracked_distance,
                    terrain_size=4.0,
                )
                self.assertFalse(move_up)
                self.assertFalse(move_down)

    def test_changing_direction_can_still_promote_from_cumulative_tracking(self):
        # This represents several well-tracked pose goals whose net world
        # displacement may be near zero after changing direction.
        move_up, move_down = classify_terrain_progress(
            completed_steps=600,
            max_episode_length=600,
            commanded_distance=1.6,
            tracked_distance=1.25,
            terrain_size=4.0,
        )
        self.assertTrue(move_up)
        self.assertFalse(move_down)


if __name__ == "__main__":
    unittest.main()
