import json
import tempfile
import unittest
import hashlib
import zipfile
from pathlib import Path

from fit_sim_from_run_data import fit_path


class FitSimFromRunDataTest(unittest.TestCase):
    def test_selects_complete_policy_runs_and_fits_known_two_frame_lag(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            complete = root / "complete.jsonl"
            incomplete = root / "incomplete.jsonl"
            no_policy = root / "stand-only.jsonl"
            self._write_run(complete, complete_session=True, with_policy=True)
            self._write_run(incomplete, complete_session=False, with_policy=True)
            self._write_run(no_policy, complete_session=True, with_policy=False)

            report = fit_path(root, max_lag_frames=3)

        self.assertEqual(report["report_schema_version"], 2)
        self.assertEqual(report["selection"]["candidate_file_count"], 3)
        self.assertEqual(report["selection"]["selected_complete_policy_run_count"], 1)
        self.assertEqual(len(report["selection"]["excluded"]), 2)
        run = report["runs"][0]
        self.assertAlmostEqual(run["timing"]["observed_hz"], 25.0)
        self.assertEqual(run["data_quality"]["missing_command_sequences"], 0)
        self.assertEqual(run["servos"]["1"]["semantic"], "test_joint")
        self.assertEqual(run["data_quality"]["current_complete_frames"], 6)
        self.assertEqual(run["servos"]["1"]["current"]["coverage_fraction"], 1.0)
        self.assertAlmostEqual(run["servos"]["1"]["current"]["abs_ma"]["max"], 39.0)
        current_fit = run["servos"]["1"]["current"]["simulation_fit"]
        self.assertEqual(current_fit["observed_clip_ma"], 39.0)
        self.assertEqual(current_fit["dropout_fraction"], 0.0)
        self.assertAlmostEqual(current_fit["dropout_probability_upper_95"], 0.5)
        self.assertEqual(run["idle_servo_telemetry"]["1"]["sample_count"], 1)
        self.assertEqual(
            run["idle_servo_telemetry"]["1"]["measurements"]["load_raw"]["min"],
            -80.0,
        )
        self.assertEqual(run["servos"]["1"]["lag_fit"]["best_lag_frames"], 2)
        self.assertAlmostEqual(run["servos"]["1"]["lag_fit"]["best_lag_ms"], 80.0)
        self.assertAlmostEqual(
            run["action_saturation"]["requested_at_actor_clip_fraction"], 0.5
        )
        self.assertEqual(len(report["identifiability_warnings"]), 5)

    def test_output_is_json_serializable_without_nonfinite_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "run.jsonl"
            self._write_run(path, complete_session=True, with_policy=True)
            report = fit_path(path, max_lag_frames=3)
            encoded = json.dumps(report, sort_keys=True, allow_nan=False)
        self.assertEqual(json.loads(encoded)["report_schema_version"], 2)

    def test_rejects_negative_lag_search(self):
        with self.assertRaisesRegex(ValueError, "nonnegative"):
            fit_path(Path("unused"), max_lag_frames=-1)

    def test_verifies_and_reads_training_capture_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            run = root / "run.jsonl"
            self._write_run(run, complete_session=True, with_policy=True)
            payload = run.read_bytes()
            capture = root / "capture.zip"
            manifest = {
                "schema_version": 1,
                "run_entry": "run/run.jsonl",
                "context": {"profile_id": "test"},
                "files": [
                    {
                        "path": "run/run.jsonl",
                        "size_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                ],
            }
            with zipfile.ZipFile(capture, "w") as archive:
                archive.writestr("run/run.jsonl", payload)
                archive.writestr("manifest.json", json.dumps(manifest))

            report = fit_path(capture, max_lag_frames=3)

        self.assertTrue(report["training_capture"]["verified"])
        self.assertEqual(report["training_capture"]["context"]["profile_id"], "test")
        self.assertEqual(report["selection"]["selected_complete_policy_run_count"], 1)

    def test_sequence_matched_targets_control_tracking_fit(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run.jsonl"
            self._write_run(run_path, complete_session=True, with_policy=True)
            records = [json.loads(line) for line in run_path.read_text().splitlines()]
            for record in records:
                if record["type"] != "derived_policy_frame":
                    continue
                data = record["data"]
                measured = data["input_robot_state"]["angles_deg"][0]
                data["input_applied_servo_target_deg"] = {"1": measured}
            run_path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8",
            )

            report = fit_path(run_path, max_lag_frames=3)

        self.assertEqual(report["runs"][0]["servos"]["1"]["error_deg"]["mae"], 0.0)

    @unittest.skipUnless(
        (
            Path(__file__).resolve().parents[2]
            / "training"
            / "diagnostics"
            / "pixel-runs-20260829"
            / "robot-run-20260829-141035-982-d14ad623.jsonl"
        ).is_file(),
        "downloaded Pixel evidence is not present",
    )
    def test_downloaded_walk_matches_calibration_golden(self):
        path = (
            Path(__file__).resolve().parents[2]
            / "training"
            / "diagnostics"
            / "pixel-runs-20260829"
            / "robot-run-20260829-141035-982-d14ad623.jsonl"
        )
        run = fit_path(path)["runs"][0]
        self.assertEqual(run["frame_count"], 1204)
        self.assertAlmostEqual(run["timing"]["observed_hz"], 25.1219722, places=5)
        self.assertAlmostEqual(run["servos"]["6"]["error_deg"]["mae"], 15.0388754, places=5)
        self.assertEqual(run["servos"]["6"]["lag_fit"]["best_lag_frames"], 12)
        self.assertAlmostEqual(run["imu"]["pitch_deg"]["mean"], 3.7289152, places=5)
        self.assertAlmostEqual(
            run["action_saturation"]["requested_at_actor_clip_fraction"],
            0.56118494,
            places=5,
        )

    @classmethod
    def _write_run(cls, path: Path, *, complete_session: bool, with_policy: bool) -> None:
        records = [
            cls._record(
                "session_start",
                0,
                1_000_000_000,
                {
                    "schema_version": 1,
                    "context": {
                        "calibration": {
                            "joints": [
                                {"servo_id": 1, "policy_index": 0, "semantic": "test_joint"}
                            ]
                        },
                        "policy_metadata": {
                            "action_contract": {
                                "applied_normalized_clip_by_joint": [0.4]
                            }
                        },
                    },
                },
            )
        ]
        if with_policy:
            targets = [0.0, 10.0, 20.0, -10.0, 5.0, 15.0]
            measured = [99.0, 98.0, 0.0, 10.0, 20.0, -10.0]
            for frame_index, (target, angle) in enumerate(zip(targets, measured), start=1):
                records.append(
                    cls._record(
                        "derived_policy_frame",
                        len(records),
                        1_000_000_000 + frame_index * 40_000_000,
                        {
                            "command_sequence": frame_index,
                            "firmware_sample_ms": 1_000 + frame_index * 40,
                            "requested_action": [1.0 if frame_index <= 3 else 0.5],
                            "applied_action": [0.4 if frame_index <= 2 else 0.2],
                            "servo_target_deg": {"1": target},
                            "inference_ms": 1.0,
                            "frame_compute_ns": 10_000_000,
                            "input_robot_state": {
                                "seq": frame_index - 1,
                                "sample_ms": 1_000 + frame_index * 40,
                                "ids": [1],
                                "angles_deg": [angle],
                                "feedback_us": 7_000,
                                "current_us": 3_000,
                                "frame_us": 8_000,
                                "feedback_complete": True,
                                "current_complete": True,
                                "current_raw": [frame_index],
                                "imu_pitch_deg": float(frame_index),
                                "imu_roll_deg": -float(frame_index),
                                "accel_mg": [0.0, 0.0, 1_000.0],
                                "gyro_dps": [1.0, -2.0, 3.0],
                            },
                        },
                    )
                )
        if complete_session:
            if with_policy:
                records.append(
                    cls._record(
                        "robot_rx",
                        len(records),
                        1_900_000_000,
                        {
                            "message": {
                                "type": "servo_telemetry",
                                "id": 1,
                                "name": "test_joint",
                                "load_raw": -80,
                                "voltage_v": 7.1,
                                "temperature_c": 21,
                                "current_ma": 26,
                            }
                        },
                    )
                )
            records.append(
                cls._record(
                    "session_end",
                    len(records),
                    2_000_000_000,
                    {"outcome": "stopped"},
                )
            )
        path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")

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
