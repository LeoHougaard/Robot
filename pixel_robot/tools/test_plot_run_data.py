import json
import tempfile
import unittest
from pathlib import Path

from plot_run_data import plot_run


class PlotRunDataTest(unittest.TestCase):
    def test_creates_current_position_and_timing_svgs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run.jsonl"
            records = []
            for index in range(3):
                records.append({
                    "type": "derived_policy_frame",
                    "data": {
                        "firmware_sample_ms": 1_000 + index * 20,
                        "frame_compute_ns": 18_000_000,
                        "inference_ms": 1.2,
                        "servo_target_deg": {"2": 100.0 + index},
                        "input_robot_state": {
                            "ids": [1, 2],
                            "angles_deg": [90.0 + index, 99.0 + index],
                            "current_raw": [index, index + 2],
                            "frame_us": 11_500,
                            "current_us": 3_200,
                        },
                    },
                })
            run.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

            outputs = plot_run(run, root / "graphs", servo_id=2)

            self.assertEqual(len(outputs), 4)
            self.assertTrue(all(path.is_file() for path in outputs))
            self.assertIn("Servo 2 current", outputs[1].read_text(encoding="utf-8"))
            self.assertIn("20 ms target", outputs[3].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
