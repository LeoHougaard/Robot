import unittest
import numpy as np

from fit_servo_response import SPEED_DEG_S, predict


class ServoResponseTests(unittest.TestCase):
    def test_speed_and_acceleration_limits_on_step_and_reversal(self):
        times = np.arange(0, 4, .005)
        targets = np.full((len(times), 12), 180.)
        targets[times >= 2] = -180
        acceleration = 263.671875
        q = predict(times, targets, np.zeros(12), [dict(
            kind="acceleration_limited", acceleration_deg_s2=acceleration, delay_s=0
        )])[:, 0]
        velocity = np.diff(q, axis=0) / .005
        self.assertLessEqual(np.max(abs(velocity)), SPEED_DEG_S + 1e-8)
        self.assertLessEqual(np.max(abs(np.diff(velocity, axis=0))) / .005, acceleration + 1e-6)
        self.assertGreater(q[400, 0], 100)
        self.assertLess(q[-1, 0], q[400, 0])

    def test_delay_is_causal_and_future_targets_cannot_change_past(self):
        times = np.arange(0, 2, .02)
        targets = np.zeros((len(times), 12))
        targets[times >= 1] = 90
        parameters = [dict(kind="first_order", tau_s=.1, delay_s=.1)]
        q = predict(times, targets, np.zeros(12), parameters)
        np.testing.assert_array_equal(q[times <= 1.1], 0)
        self.assertGreater(q[-1, 0, 0], 0)
        targets[times >= 1.5] = -90
        changed = predict(times, targets, np.zeros(12), parameters)
        np.testing.assert_array_equal(changed[times < 1.5], q[times < 1.5])

    def test_rejects_corrupt_samples(self):
        for times in ([0, 0], [0, float("nan")], [1, 0]):
            with self.assertRaises(ValueError):
                predict(times, np.zeros((2, 12)), np.zeros(12), [
                    dict(kind="first_order", tau_s=.1, delay_s=0)])


if __name__ == "__main__":
    unittest.main()
