from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

from run_policy import NumpyPolicy, RobotCalibration


class PolicyRuntimeTests(unittest.TestCase):
    def test_template_calibration_cannot_arm(self) -> None:
        path = Path(__file__).with_name("assembly-1-12dof.calibration.json")
        with self.assertRaisesRegex(ValueError, "calibration flags"):
            RobotCalibration(path)

    def test_portable_actor_enforces_hash_and_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            weights = root / "policy_weights.npz"
            arrays = {
                "obs_mean": np.zeros(180, dtype=np.float32),
                "obs_var": np.ones(180, dtype=np.float32),
                "w0": np.zeros((128, 180), dtype=np.float32),
                "b0": np.zeros(128, dtype=np.float32),
                "w1": np.zeros((128, 128), dtype=np.float32),
                "b1": np.zeros(128, dtype=np.float32),
                "w2": np.zeros((128, 128), dtype=np.float32),
                "b2": np.zeros(128, dtype=np.float32),
                "wout": np.zeros((12, 128), dtype=np.float32),
                "bout": np.linspace(-2.0, 2.0, 12, dtype=np.float32),
            }
            np.savez_compressed(weights, **arrays)
            digest = hashlib.sha256(weights.read_bytes()).hexdigest()
            metadata = root / "policy_metadata.json"
            metadata.write_text(
                json.dumps(
                    {
                        "weights_sha256": digest,
                        "profile_id": "assembly-1-12dof",
                        "profile_sha256": "b25a4a05fa5a6439b82b824d2c2c826f2a9cc5aacc274d75ee8b4d39978035d3",
                        "observation_size": 180,
                        "action_size": 12,
                        "control_hz": 50,
                        "action_scale_rad": 0.25,
                        "validated_command_limits": {
                            "forward_m_s": [0.0, 0.18],
                            "lateral_m_s": [0.0, 0.0],
                            "yaw_rate_rad_s": [-0.25, 0.25],
                        },
                        "evaluation": {"passed": True, "stage": "goal"},
                    }
                ),
                encoding="utf-8",
            )
            policy = NumpyPolicy(weights, metadata)
            action = policy.action(np.zeros(180, dtype=np.float32))
            self.assertEqual(action.shape, (12,))
            self.assertAlmostEqual(float(action[0]), -1.0)
            self.assertAlmostEqual(float(action[-1]), 1.0)

            metadata_value = json.loads(metadata.read_text(encoding="utf-8"))
            metadata_value["weights_sha256"] = "0" * 64
            metadata.write_text(json.dumps(metadata_value), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "weight hash"):
                NumpyPolicy(weights, metadata)

    def test_historical_rear_knee_profile_is_rejected(self) -> None:
        path = Path(__file__).with_name("policy_metadata.json")
        metadata = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotEqual(
            metadata["profile_sha256"],
            "615092eb851641d55c7a919e6798bc6a8a119669ee201d1fa73afb2dab8d5b98",
        )


if __name__ == "__main__":
    unittest.main()
