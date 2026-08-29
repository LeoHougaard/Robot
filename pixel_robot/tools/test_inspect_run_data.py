import json
import tempfile
import unittest
from pathlib import Path

from inspect_run_data import summarize_run


class InspectRunDataTest(unittest.TestCase):
    def test_summary_validates_envelope_and_reports_physical_peaks(self):
        records = [
            self._record("session_start", 0, 1_000_000_000, {"schema_version": 1}),
            self._record(
                "derived_policy_frame",
                1,
                1_020_000_000,
                {
                    "command_sequence": 1,
                    "tracking_error_deg": 2.5,
                    "inference_ms": 1.2,
                    "input_robot_state": {
                        "ids": [1, 2],
                        "imu_pitch_deg": -8.0,
                        "voltage_tenths": [120, 118],
                        "temperature_c": [31, 34],
                        "current_raw": [-7, 4],
                        "current_complete": True,
                        "current_us": 3_200,
                        "feedback_us": 7_000,
                        "frame_us": 11_500,
                        "load_raw": [12, -20],
                        "feedback_complete": True,
                    },
                },
            ),
            self._record(
                "derived_policy_frame",
                2,
                1_040_000_000,
                {"command_sequence": 3, "tracking_error_deg": 4.0, "inference_ms": 2.0},
            ),
            self._record(
                "robot_rx",
                3,
                1_050_000_000,
                {
                    "message": {
                        "type": "servo_telemetry",
                        "id": 2,
                        "name": "test servo",
                        "load_raw": -80,
                        "voltage_v": 7.1,
                        "temperature_c": 21,
                        "current_ma": 26,
                    }
                },
            ),
            self._record("session_end", 4, 1_060_000_000, {"outcome": "stopped"}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.jsonl"
            path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
            summary = summarize_run(path)

        self.assertTrue(summary["complete"])
        self.assertEqual(summary["policy_frames"], 2)
        self.assertEqual(summary["missing_command_sequences"], 1)
        self.assertEqual(summary["max_abs_pitch_deg"], 8.0)
        self.assertEqual(summary["minimum_servo_voltage_v"], 11.8)
        self.assertEqual(summary["maximum_abs_servo_load_raw"], 20.0)
        self.assertEqual(summary["current_complete_frames"], 1)
        self.assertEqual(summary["servo_current"]["1"]["coverage_fraction"], 1.0)
        self.assertEqual(summary["servo_current"]["1"]["maximum_abs_ma"], 45.5)
        self.assertEqual(summary["firmware_current_read_ms"]["median"], 3.2)
        self.assertFalse(summary["transport_50hz_gate"]["passed"])
        self.assertEqual(summary["idle_servo_telemetry"]["2"]["sample_count"], 1)
        self.assertEqual(
            summary["idle_servo_telemetry"]["2"]["measurements"]["load_raw"]["minimum"],
            -80.0,
        )

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
