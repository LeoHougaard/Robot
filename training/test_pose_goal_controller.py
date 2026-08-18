"""Focused tests for the deployable planar pose controller."""

from __future__ import annotations

import math
import unittest

import torch

from pose_goal_controller import pose_error_to_velocity_command


class PoseGoalControllerTests(unittest.TestCase):
    def command(self, x: float, y: float, yaw: float) -> torch.Tensor:
        return pose_error_to_velocity_command(
            torch.tensor([[x, y]], dtype=torch.float32),
            torch.tensor([yaw], dtype=torch.float32),
            max_forward_speed=0.25,
            max_reverse_speed=0.20,
            max_lateral_speed=0.18,
            max_yaw_rate=0.30,
            position_tolerance=0.10,
            heading_tolerance=0.12,
            distance_gain=0.80,
            final_heading_gain=1.50,
        )[0]

    def test_drives_straight_toward_a_distant_goal(self):
        command = self.command(1.0, 0.0, 0.0)
        torch.testing.assert_close(command, torch.tensor([0.25, 0.0, 0.0]))

    def test_reverses_toward_a_goal_behind(self):
        command = self.command(-1.0, 0.0, 0.0)
        torch.testing.assert_close(command, torch.tensor([-0.20, 0.0, 0.0]))

    def test_strafes_toward_a_side_goal(self):
        command = self.command(0.0, 1.0, 0.0)
        torch.testing.assert_close(command, torch.tensor([0.0, 0.18, 0.0]))

    def test_combines_translation_and_rotation(self):
        command = self.command(1.0, -1.0, 0.5)
        torch.testing.assert_close(command, torch.tensor([0.25, -0.18, 0.30]))

    def test_settles_final_heading_after_reaching_xy(self):
        command = self.command(0.05, 0.0, -0.50)
        torch.testing.assert_close(command, torch.tensor([0.0, 0.0, -0.30]))

    def test_stops_inside_both_tolerances(self):
        command = self.command(0.05, -0.02, 0.05)
        torch.testing.assert_close(command, torch.zeros(3))

    def test_wraps_final_heading_across_pi(self):
        command = self.command(0.0, 0.0, math.pi + 0.10)
        self.assertAlmostEqual(command[2].item(), -0.30, places=5)


if __name__ == "__main__":
    unittest.main()
