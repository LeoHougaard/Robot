import json
import tempfile
import unittest
from pathlib import Path

from current_policy_fit import load_current_policy_fit


SEMANTICS = tuple(f"joint_{index}" for index in range(12))


class CurrentPolicyFitTest(unittest.TestCase):
    def test_maps_servo_fit_by_semantic_not_servo_id(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fit.json"
            path.write_text(json.dumps(self._fit()), encoding="utf-8")
            fit = load_current_policy_fit(path, SEMANTICS)

        self.assertEqual(fit.servo_ids, tuple(range(12, 0, -1)))
        self.assertEqual(fit.current_bias_ma[0], 1.0)
        self.assertEqual(fit.current_bias_ma[-1], 12.0)
        self.assertEqual(fit.command_delay_steps, (0, 2))
        self.assertEqual(fit.current_delay_steps, (0, 1))
        self.assertFalse(fit.transport_50hz_passed)

    def test_rejects_an_old_fit_schema(self):
        report = self._fit()
        report["report_schema_version"] = 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fit.json"
            path.write_text(json.dumps(report), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "schema 2"):
                load_current_policy_fit(path, SEMANTICS)

    @staticmethod
    def _fit():
        servos = {}
        for index, semantic in enumerate(SEMANTICS, start=1):
            servo_id = 13 - index
            servos[str(servo_id)] = {
                "semantic": semantic,
                "current": {"simulation_fit": {
                    "normalization_bias_ma": float(index),
                    "normalization_scale_ma": 50.0,
                    "observed_clip_ma": 200.0,
                    "difference_mad_ma": 6.5,
                    "dropout_fraction": 0.0,
                    "dropout_probability_upper_95": 0.01,
                }},
                "measured_speed_abs_deg_s": {"p95": 90.0},
                "lag_fit": {
                    "best_lag_ms": 100.0,
                    "aligned_bias_deg": 1.0,
                    "aligned_residual_mad_deg": 2.0,
                },
            }
        return {
            "report_schema_version": 2,
            "runs": [{
                "frame_count": 2189,
                "duration_s": 50.727,
                "data_quality": {
                    "incomplete_feedback_frames": 0,
                    "current_complete_frames": 2189,
                },
                "timing": {
                    "observed_hz": 43.13,
                    "command_to_feedback_ms": {"min": 4.0, "p95": 29.0},
                    "firmware_current_read_ms": {"min": 3.2, "p95": 3.4},
                },
                "transport_50hz_gate": {"passed": False},
                "physical_context": {
                    "gyro_bias_sensor_dps": [0.1, 0.2, 0.3],
                    "servo_battery_voltage_v": 7.4,
                },
                "imu": {
                    "gyro_body_rad_s_xyz": [{"p95": 1.0}] * 3,
                    "projected_gravity_body_xyz": [{"p95": 0.1}] * 3,
                },
                "action_saturation": {
                    "requested_at_actor_clip_fraction": 0.63,
                    "requested_at_actor_clip_fraction_by_joint": [0.5] * 12,
                    "applied_at_safety_clip_fraction_by_joint": [0.1] * 12,
                },
                "servos": servos,
            }],
        }


if __name__ == "__main__":
    unittest.main()
