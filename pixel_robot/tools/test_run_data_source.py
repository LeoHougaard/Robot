import hashlib
import json
import tempfile
import unittest
from pathlib import Path
import zipfile

from run_data_source import open_run_text, verify_training_capture


class RunDataSourceTest(unittest.TestCase):
    def test_opens_verified_training_capture_run(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture.zip"
            run = b'{"type":"session_start"}\n'
            self._write_capture(capture, run)

            manifest = verify_training_capture(capture)
            with open_run_text(capture) as (stream, label):
                text = stream.read()

        self.assertEqual(manifest["run_entry"], "run/test.jsonl")
        self.assertEqual(text.encode(), run)
        self.assertIn("!/run/test.jsonl", label)

    def test_rejects_tampered_run(self):
        with tempfile.TemporaryDirectory() as directory:
            capture = Path(directory) / "capture.zip"
            self._write_capture(capture, b"changed", declared=b"original")

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                verify_training_capture(capture)

    @staticmethod
    def _write_capture(capture: Path, run: bytes, declared: bytes | None = None) -> None:
        expected = run if declared is None else declared
        manifest = {
            "schema_version": 1,
            "run_entry": "run/test.jsonl",
            "files": [
                {
                    "path": "run/test.jsonl",
                    "size_bytes": len(run),
                    "sha256": hashlib.sha256(expected).hexdigest(),
                }
            ],
        }
        with zipfile.ZipFile(capture, "w") as archive:
            archive.writestr("run/test.jsonl", run)
            archive.writestr("manifest.json", json.dumps(manifest))


if __name__ == "__main__":
    unittest.main()
