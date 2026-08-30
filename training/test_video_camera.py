"""Tests for deterministic multi-robot rollout camera selection."""

import unittest

from video_camera import select_video_camera_sample, stratified_env_indices


class VideoCameraTests(unittest.TestCase):
    def test_stratifies_across_full_scene(self):
        self.assertEqual(stratified_env_indices(512), (0, 128, 256, 383, 511))

    def test_rotates_robot_and_view_per_clip(self):
        samples = [
            select_video_camera_sample(step, 2000, 512)
            for step in (0, 2000, 4000, 6000, 8000)
        ]
        self.assertEqual([sample.env_index for sample in samples], [0, 128, 256, 383, 511])
        self.assertEqual([sample.view_index for sample in samples], [0, 1, 2, 3, 4])

    def test_wraps_without_out_of_range_index(self):
        sample = select_video_camera_sample(10_000, 2000, 3)
        self.assertEqual(sample.env_index, 2)

    def test_rejects_invalid_ranges(self):
        with self.assertRaises(ValueError):
            stratified_env_indices(0)
        with self.assertRaises(ValueError):
            select_video_camera_sample(0, 0, 1)


if __name__ == "__main__":
    unittest.main()
