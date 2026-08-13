from pathlib import Path
import tempfile
import unittest

from control_center.server import load_or_create_session_token


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


if __name__ == "__main__":
    unittest.main()
