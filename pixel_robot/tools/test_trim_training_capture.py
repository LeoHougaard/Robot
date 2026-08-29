import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from run_data_source import verify_training_capture
from trim_training_capture import trim_capture


class TrimTrainingCaptureTest(unittest.TestCase):
    def test_trims_to_policy_time_and_preserves_package_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.zip"
            records = [{
                "type": "session_start", "session_id": "source", "record_index": 0,
                "host_unix_ms": 1, "host_monotonic_ns": 1,
                "data": {"context": {}},
            }]
            for index, ns in enumerate((1_000_000_000, 2_000_000_000, 3_000_000_000), 1):
                records.append({
                    "type": "derived_policy_frame", "session_id": "source",
                    "record_index": index, "host_unix_ms": index * 1000,
                    "host_monotonic_ns": ns, "data": {},
                })
            records.append({
                "type": "session_end", "session_id": "source", "record_index": 4,
                "host_unix_ms": 4000, "host_monotonic_ns": 4_000_000_000,
                "data": {"outcome": "complete"},
            })
            run = "".join(json.dumps(item) + "\n" for item in records).encode()
            policy = b"policy"
            manifest = {
                "schema_version": 1, "created_unix_ms": 1,
                "run_entry": "run/source.jsonl", "context": {},
                "files": [
                    {"path": "run/source.jsonl", "size_bytes": len(run),
                     "sha256": hashlib.sha256(run).hexdigest()},
                    {"path": "policy/model.bin", "size_bytes": len(policy),
                     "sha256": hashlib.sha256(policy).hexdigest()},
                ],
            }
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("run/source.jsonl", run)
                archive.writestr("policy/model.bin", policy)
                archive.writestr("manifest.json", json.dumps(manifest))
            output_capture = root / "trimmed.zip"
            output_run = root / "trimmed.jsonl"
            result = trim_capture(
                source, output_capture, output_run,
                start_seconds=0.0, end_seconds=1.5, reason="test trim",
            )
            verified = verify_training_capture(output_capture)
            trimmed = [json.loads(line) for line in output_run.read_text().splitlines()]
            self.assertEqual(verified["run_entry"], "run/trimmed.jsonl")
            self.assertEqual([item["record_index"] for item in trimmed], list(range(4)))
            self.assertEqual(trimmed[0]["type"], "session_start")
            self.assertEqual(trimmed[-1]["type"], "session_end")
            self.assertEqual(
                sum(item["type"] == "derived_policy_frame" for item in trimmed), 2
            )
            self.assertEqual(result["trim"]["retained_policy_frames"], 2)
            with zipfile.ZipFile(output_capture) as archive:
                self.assertEqual(archive.read("policy/model.bin"), policy)


if __name__ == "__main__":
    unittest.main()
