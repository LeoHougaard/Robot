from __future__ import annotations

import math
import unittest

from goal_controller import GoalController, Pose2D, wrap_angle


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


if __name__ == "__main__":
    unittest.main()
