from pathlib import Path
import tempfile
import unittest

from control_center.server import (
    ControlCenter,
    load_or_create_session_token,
    load_review_videos,
    rendered_checkpoint_epoch_from_output,
)


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
    def test_renderer_epoch_is_not_confused_with_v5_name(self) -> None:
        self.assertEqual(
            75,
            rendered_checkpoint_epoch_from_output(
                "Validation sample: 5/5\nCheckpoint epoch: 75\n"
            ),
        )

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

    def test_review_cache_isolated_by_exact_experiment(self) -> None:
        center = ControlCenter.__new__(ControlCenter)
        center._review_cache_experiment = "2026-08-30_20-21-01"
        center._review_sample_cache = {0: {"source": "old-v4"}}
        center._review_render_queue = [1, 2]
        center._last_rendered_sample_index = 0

        center._activate_review_experiment("2099-12-31_23-59-59")

        self.assertEqual(center._review_cache_experiment, "2099-12-31_23-59-59")
        self.assertEqual(center._review_sample_cache, {})
        self.assertEqual(center._review_render_queue, [])

    def test_active_review_retries_same_sample_twice(self) -> None:
        center = ControlCenter.__new__(ControlCenter)
        calls = []
        results = iter(
            [
                {"ok": False, "exit_code": 1, "output": "startup crashed"},
                {"ok": False, "exit_code": 1, "output": "vpn dropped"},
                {"ok": True, "exit_code": 0, "output": "complete"},
            ]
        )

        def run_script(script, arguments, *, timeout):
            calls.append((script, list(arguments), timeout))
            return next(results)

        center._run_script = run_script
        result = center._render_active_review(
            {"training": {"video_length": 600}}, 4
        )

        self.assertTrue(result["ok"])
        self.assertEqual(3, len(calls))
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(calls[1], calls[2])
        self.assertEqual(
            [
                "-VideoLength",
                "600",
                "-ValidationSample",
                "4",
                "-ReuseDeployedSource",
            ],
            calls[0][1],
        )


if __name__ == "__main__":
    unittest.main()
