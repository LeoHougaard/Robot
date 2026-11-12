"""Direction, completeness and finite-evidence gates for the fixed slow screen."""
import copy
import unittest
from delivery_contract import SEGMENTS
from evaluate_simple_dog_policy import evaluate
from test_evaluate_simple_dog_policy import ideal_segment


def ideal_delivery():
    records = {}
    for name, steps, forward, lateral, yaw, height, roll, pitch in SEGMENTS:
        item = ideal_segment(name, step_dt=.02)
        item.update(steps=steps, command_forward=forward, command_lateral=lateral,
                    command_yaw=yaw, command_height_offset=height, command_roll=roll,
                    command_pitch=pitch, mean_body_forward=forward, mean_body_lateral=lateral,
                    mean_abs_body_lateral=abs(lateral), mean_yaw_rate=yaw,
                    forward_displacement=forward*steps*.02, lateral_displacement=lateral*steps*.02,
                    heading_delta=yaw*steps*.02)
        records[name] = item
    return records


class DeliveryEvaluationTests(unittest.TestCase):
    def test_ideal_screen_passes(self):
        self.assertTrue(evaluate("deliveryflat", ideal_delivery())["passed"])

    def test_wrong_direction_stale_screen_nan_and_final_fall_rejected(self):
        for name, change in (
            ("reverse", {"mean_body_forward": .08}),
            ("forward", {"command_forward": .18}),
            ("forward", {"mean_body_forward": float("nan")}),
            ("current_dropout_walk", {"resets": 1}),
            ("stop", {"mean_body_forward": .03}),
            ("forward", {"mean_foot_slip": .10}),
            ("crouch_walk", {"command_height_offset": 0.}),
        ):
            with self.subTest(name=name, change=change):
                records = copy.deepcopy(ideal_delivery())
                records[name].update(change)
                self.assertFalse(evaluate("deliveryflat", records)["passed"])


if __name__ == "__main__":
    unittest.main()
