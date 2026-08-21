from pathlib import Path
import tempfile
import unittest

from control_center.server import load_or_create_session_token, video_metadata_matches


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


class VideoMetadataTests(unittest.TestCase):
    def test_matching_playback_metadata_is_accepted(self) -> None:
        fields = {
            "task": "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0",
            "profile": "assembly-four-leg-linkage-12dof",
            "surface": "Pyramid stairs",
        }
        metadata = {
            "task": "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-Play-v0",
            "profile_id": "assembly-four-leg-linkage-12dof",
            "surface": "Pyramid stairs",
        }
        self.assertTrue(video_metadata_matches(metadata, fields))

    def test_stale_flat_video_is_rejected(self) -> None:
        fields = {
            "task": "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0",
            "profile": "assembly-four-leg-linkage-12dof",
            "surface": "Pyramid stairs",
        }
        metadata = {
            "task": "Isaac-Locomotion-V2-Simple-Dog-Direct-Play-v0",
            "profile_id": "assembly-four-leg-linkage-12dof",
            "surface": "Flat",
        }
        self.assertFalse(video_metadata_matches(metadata, fields))


if __name__ == "__main__":
    unittest.main()
