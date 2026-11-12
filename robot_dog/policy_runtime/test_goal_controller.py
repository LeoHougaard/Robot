from __future__ import annotations

import math
import unittest

from goal_controller import GoalController, GoalControllerConfig, Pose2D, wrap_angle


class GoalControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = GoalController()

    def test_walks_forward_toward_aligned_goal(self) -> None:
        command = self.controller.command(Pose2D(0, 0, 0), Pose2D(1, 0, 0))
        self.assertAlmostEqual(command.forward, 0.18)
        self.assertAlmostEqual(command.lateral, 0.0)
        self.assertAlmostEqual(command.yaw_rate, 0.0)
        self.assertFalse(command.reached)

    def test_rotates_before_goal_behind_robot(self) -> None:
        command = self.controller.command(Pose2D(0, 0, 0), Pose2D(-1, 0, 0))
        self.assertAlmostEqual(command.forward, 0.0)
        self.assertAlmostEqual(abs(command.yaw_rate), 0.25)

    def test_finishes_goal_heading_after_position(self) -> None:
        command = self.controller.command(
            Pose2D(1.02, 0.01, 0), Pose2D(1, 0, math.pi / 2)
        )
        self.assertAlmostEqual(command.forward, 0.0)
        self.assertAlmostEqual(command.yaw_rate, 0.25)
        self.assertFalse(command.reached)

    def test_reports_reached_only_for_position_and_heading(self) -> None:
        command = self.controller.command(
            Pose2D(1.02, 0.01, 0.05), Pose2D(1, 0, 0)
        )
        self.assertTrue(command.reached)
        self.assertEqual(command.forward, 0.0)
        self.assertEqual(command.yaw_rate, 0.0)

    def test_angle_wrap_uses_short_rotation(self) -> None:
        error = wrap_angle(math.radians(-179) - math.radians(179))
        self.assertAlmostEqual(error, math.radians(2))

    def test_nonfinite_pose_and_goal_are_rejected(self) -> None:
        for invalid in (math.nan, math.inf, -math.inf):
            for index in range(3):
                values = [0., 0., 0.]
                values[index] = invalid
                for pose, goal in ((Pose2D(*values), Pose2D(0, 0, 0)),
                                   (Pose2D(0, 0, 0), Pose2D(*values))):
                    with self.subTest(pose=pose, goal=goal), self.assertRaises(ValueError):
                        self.controller.command(pose, goal)

    def test_invalid_controller_configuration_is_rejected(self) -> None:
        for name in vars(GoalControllerConfig()):
            for value in (math.nan, math.inf, -.1):
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    GoalController(GoalControllerConfig(**{name: value}))

    def test_finite_extreme_yaw_difference_cannot_become_a_motion_command(self) -> None:
        with self.assertRaises(ValueError):
            self.controller.command(Pose2D(0, 0, -1e308), Pose2D(0, 0, 1e308))

    def test_finite_extreme_position_difference_cannot_overflow(self) -> None:
        with self.assertRaises(ValueError):
            self.controller.command(Pose2D(-1e308, 0, 0), Pose2D(1e308, 0, 0))

    def test_distance_and_heading_boundaries_use_meters_and_radians(self) -> None:
        cfg = self.controller.config
        self.assertTrue(self.controller.command(
            Pose2D(0, 0, 0), Pose2D(cfg.position_tolerance_m, 0, 0)
        ).reached)
        for yaw in (-math.pi, -.71, -.69, 0., .69, .71, math.pi):
            command = self.controller.command(Pose2D(0, 0, yaw), Pose2D(10, 0, 0))
            self.assertGreaterEqual(command.forward, 0.)
            self.assertLessEqual(command.forward, .18)
            self.assertLessEqual(abs(command.yaw_rate), .25)
            self.assertEqual(command.lateral, 0.)


if __name__ == "__main__":
    unittest.main()
