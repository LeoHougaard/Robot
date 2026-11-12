"""Check the production height measurement independently of foot geometry."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

import torch


tree = ast.parse((Path(__file__).parent / "simple_dog_task_current_body_v20/env.py").read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_body_posture")
namespace = {"torch": torch}
exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
             "delivery_height", "exec"), namespace)


def measure(root_z, terrain_z, gravity=(0., 0., -1.)):
    wrap = lambda values: NS(torch=torch.tensor(values, dtype=torch.float32))
    env = NS(
        _robot=NS(data=NS(root_pos_w=wrap([[0., 0., root_z]]),
                          projected_gravity_b=wrap([gravity]))),
        _height_scanner=NS(data=NS(ray_hits_w=wrap([[[0., 0., z] for z in terrain_z]]))),
        _semantic_vector_b=lambda value: value,
    )
    # No foot COM input exists: the real method must measure terrain itself.
    return namespace["_body_posture"](env)


class GroundHeightTests(unittest.TestCase):
    def test_flat_and_raised_ground_have_same_clearance(self):
        self.assertAlmostEqual(measure(.18, [0.] * 25)[0].item(), .18, places=6)
        self.assertAlmostEqual(measure(1.18, [1.] * 25)[0].item(), .18, places=6)

    def test_uneven_terrain_uses_mean_support_surface(self):
        self.assertAlmostEqual(measure(.18, [0., .002, .004])[0].item(), .178, places=6)

    def test_body_attitude_remains_gravity_relative(self):
        height, roll, pitch = measure(.18, [.003] * 25, (0., .1, -.994987437))
        self.assertAlmostEqual(height.item(), .177, places=6)
        self.assertAlmostEqual(roll.item(), .100167421, places=6)
        self.assertEqual(pitch.item(), 0.)

    def test_missing_ground_fails_instead_of_rewarding_fake_height(self):
        for value in (float("inf"), float("nan"), -float("inf")):
            with self.assertRaisesRegex(RuntimeError, "missed terrain"):
                measure(.18, [0., value])


if __name__ == "__main__":
    unittest.main()
