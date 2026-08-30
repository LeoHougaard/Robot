from pathlib import Path
import tempfile
import unittest

from control_center.server import ControlCenter, load_or_create_session_token, load_review_videos


class SessionTokenTests(unittest.TestCase):
    def test_token_survives_server_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session-token"
            first = load_or_create_session_token(path)
            second = load_or_create_session_token(path)
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 32)

    def test_malformed_token_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session-token"
            path.write_text("bad token\n", encoding="utf-8")
            token = load_or_create_session_token(path)
            self.assertNotEqual("bad token", token)
            self.assertTrue(all(char.isalnum() or char in "-_" for char in token))


class ReviewManifestTests(unittest.TestCase):
    def test_only_existing_safe_mp4_entries_are_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "good.mp4").write_bytes(b"video")
            manifest = root / "review-index.json"
            manifest.write_text(
                '{"videos": ['
                '{"id":"epoch-25","file":"good.mp4","epoch":25},'
                '{"id":"../escape","file":"good.mp4"},'
                '{"id":"missing","file":"missing.mp4"},'
                '{"id":"outside","file":"../outside.mp4"}'
                ']}',
                encoding="utf-8",
            )
            videos = load_review_videos(manifest)
            self.assertEqual(["epoch-25"], [item["id"] for item in videos])
            self.assertEqual("/api/video/review/epoch-25", videos[0]["video_url"])


class ReviewSampleTests(unittest.TestCase):
    def test_render_cycle_uses_all_five_before_repeating(self) -> None:
        center = ControlCenter.__new__(ControlCenter)
        center._last_rendered_sample_index = None
        center._review_sample_cache = {index: {} for index in range(5)}
        center._review_render_queue = []

        first_cycle = []
        for _ in range(5):
            sample = center._next_review_sample_index()
            first_cycle.append(sample)
            center._last_rendered_sample_index = sample

        self.assertEqual(set(range(5)), set(first_cycle))
        next_sample = center._next_review_sample_index()
        self.assertNotEqual(first_cycle[-1], next_sample)

    def test_cached_review_cycle_presents_all_five_before_repeating(self) -> None:
        center = ControlCenter.__new__(ControlCenter)
        center._review_sample_cache = {index: {} for index in range(5)}
        center._review_presentation_queue = []
        center._last_presented_sample_index = None

        first_cycle = []
        for _ in range(5):
            sample = center._next_cached_review_sample_index()
            first_cycle.append(sample)
            center._last_presented_sample_index = sample

        self.assertEqual(set(range(5)), set(first_cycle))
        next_sample = center._next_cached_review_sample_index()
        self.assertNotEqual(first_cycle[-1], next_sample)


if __name__ == "__main__":
    unittest.main()
