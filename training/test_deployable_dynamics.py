import json
from pathlib import Path
import unittest

import torch
from deployable_dynamics import ServoTrajectory, gravity_estimate, motor_to_policy, policy_to_motor


class DeployableDynamicsTests(unittest.TestCase):
    def test_coordinates_match_documented_encoder_parent_transform(self):
        q = torch.arange(12, dtype=torch.float32).reshape(1, 12) / 10
        motor = policy_to_motor(q)
        self.assertAlmostEqual(float(motor[0, 2]), .3, places=6)
        self.assertAlmostEqual(float(motor[0, 11]), 2.1, places=6)
        torch.testing.assert_close(motor_to_policy(motor), q)

    def test_servo_limits_apply_to_motor_and_survive_partial_reset(self):
        fit = json.loads((Path(__file__).parent / "fits/servo-response-20260829.json").read_text())
        servo = ServoTrajectory(fit, 2, "cpu")
        servo.reset(torch.tensor([0, 1]), torch.zeros(2, 12))
        servo.command(torch.ones(2, 12))
        for _ in range(100):
            previous_velocity = servo.velocity.clone()
            servo.step(.005)
            self.assertTrue(torch.all(servo.velocity.abs() <= servo.speed + 1e-5))
            self.assertTrue(torch.all((servo.velocity - previous_velocity).abs() <= servo.acceleration * .005 + 1e-5))
        retained = servo.position[1].clone()
        servo.reset(torch.tensor([0]), torch.full((1, 12), .1))
        torch.testing.assert_close(servo.position[1], retained)
        torch.testing.assert_close(motor_to_policy(servo.position[0]), torch.full((12,), .1))

    def test_gravity_rejects_dynamic_acceleration_correction_and_rotates(self):
        prior = torch.tensor([[0., 0, -1.]])
        gyro = torch.tensor([[1., 0, 0]])
        result = gravity_estimate(prior, torch.tensor([[500., 0, 1500.]]), gyro, .02, torch.tensor([False]))
        expected = torch.tensor([[0., -.02, -1.]])
        expected /= expected.norm(dim=-1, keepdim=True)
        torch.testing.assert_close(result, expected)
        fresh = gravity_estimate(prior, torch.tensor([[0., 0, 1000.]]), gyro, .02, torch.tensor([True]))
        torch.testing.assert_close(fresh, prior)


if __name__ == "__main__":
    unittest.main()
