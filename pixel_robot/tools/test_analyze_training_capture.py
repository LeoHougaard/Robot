import json
import tempfile
import unittest
from pathlib import Path

from analyze_training_capture import analyze_capture


class AnalyzeTrainingCaptureTest(unittest.TestCase):
    def test_writes_fit_summary_and_graphs_for_clocked_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run.jsonl"
            records = [self._record("session_start", 0, 1_000_000_000, {"schema_version": 1})]
            for index in range(3):
                records.append(
                    self._record(
                        "derived_policy_frame",
                        len(records),
                        1_020_000_000 + index * 20_000_000,
                        {
                            "command_sequence": index + 1,
                            "input_state_sequence": index,
                            "input_feedback_tick": index,
                            "firmware_sample_ms": 1_000 + index * 20,
                            "servo_target_deg": {"2": 100.0 + index},
                            "input_applied_servo_target_deg": {"2": 99.0 + index},
                            "requested_action": [0.1],
                            "applied_action": [0.1],
                            "inference_ms": 1.0,
                            "frame_compute_ns": 18_000_000,
                            "command_to_feedback_ns": 12_000_000,
                            "input_robot_state": {
                                "seq": index,
                                "tick": index,
                                "sample_ms": 1_000 + index * 20,
                                "ids": [2],
                                "angles_deg": [99.0 + index],
                                "current_raw": [index + 1],
                                "feedback_complete": True,
                                "current_complete": True,
                                "feedback_us": 7_000,
                                "current_us": 3_000,
                                "frame_us": 11_000,
                                "accel_mg": [0.0, 0.0, 1_000.0],
                                "gyro_dps": [0.0, 0.0, 0.0],
                            },
                        },
                    )
                )
            records.append(
                self._record("session_end", len(records), 1_100_000_000, {"outcome": "stopped"})
            )
            run.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            result = analyze_capture(run, root / "analysis", servo_id=2)

            self.assertTrue(result["transport_50hz_gate"]["passed"])
            self.assertTrue(Path(result["summary"]).is_file())
            self.assertTrue(Path(result["simulation_fit"]).is_file())
            self.assertEqual(len(result["graphs"]), 4)
            dashboard = Path(result["dashboard"])
            self.assertTrue(dashboard.is_file())
            page = dashboard.read_text(encoding="utf-8")
            self.assertIn("servo-current-all.svg", page)
            self.assertIn("servo-2-current.svg", page)

    @staticmethod
    def _record(record_type, index, monotonic_ns, data):
        return {
            "type": record_type,
            "session_id": "test-session",
            "record_index": index,
            "host_unix_ms": 1_000 + index,
            "host_monotonic_ns": monotonic_ns,
            "data": data,
        }


if __name__ == "__main__":
    unittest.main()
