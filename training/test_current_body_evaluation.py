"""CPU regression tests for the real evaluation methods, without starting Isaac.

Load selected methods with AST because importing task packages starts Isaac.
Run with a Python environment containing torch.
"""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest

import torch

ROOT = Path(__file__).resolve().parent


def extract_class(path, name, methods, bases, namespace):
    tree = ast.parse((ROOT / path).read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)
    cls.body = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in methods]
    cls.bases = [ast.Name(id=b, ctx=ast.Load()) for b in bases]
    module = ast.fix_missing_locations(ast.Module(body=[cls], type_ignores=[]))
    exec(compile(module, str(path), "exec"), namespace)
    return namespace[name]


class Parent:
    def _get_dones(self):
        return torch.tensor([True]), torch.tensor([False])


# Identity orientation fixtures; no simulator math is needed for these cases.
namespace = dict(torch=torch, Parent=Parent, quat_apply=lambda q, v: v)
V2 = extract_class("simple_dog_task_v2/simple_dog_v2_env.py", "SimpleDogV2Env",
    {"_begin_evaluation_segment", "_record_evaluation_step", "_accumulate_evaluation_extra_metrics"},
    ["Parent"], namespace)
Current = extract_class("simple_dog_task_current/simple_dog_current_env.py", "SimpleDogCurrentV3Env",
    {"_begin_evaluation_segment", "_accumulate_evaluation_extra_metrics"}, ["SimpleDogV2Env"], namespace)
Body = extract_class("simple_dog_task_current_body_v4/simple_dog_current_body_v4_env.py", "SimpleDogCurrentBodyV4Env",
    {"_get_dones", "_record_body_evaluation_step"}, ["SimpleDogCurrentV3Env"], namespace)


def wrapped(value):
    return NS(torch=torch.tensor(value, dtype=torch.float32))


def fixture():
    env = Body()
    env.cfg = NS(nominal_support_height_m=0.2, nominal_foot_com_z_from_base_m=-0.2)
    env._robot = NS(data=NS(
        root_pos_w=wrapped([[0, 0, 0.2]]), root_quat_w=wrapped([[1, 0, 0, 0]]),
        root_lin_vel_b=wrapped([[0.1, 0.02, 0.03]]), root_ang_vel_b=wrapped([[0, 0, 0.04]]),
        projected_gravity_b=wrapped([[0, 0, -1]]),
        body_com_pos_w=wrapped([[[0, 0.1, 0], [0, -0.1, 0], [0, 0.1, 0.05], [0, -0.1, 0.05]]]),
        body_lin_vel_w=wrapped([[[0.1, 0, 0], [0.3, 0, 0], [5, 0, 0], [5, 0, 0]]]),
    ))
    env._contact_sensor = NS(data=NS(net_forces_w_history=wrapped([[[[0, 0, 2], [0, 0, 2], [0, 0, 0], [0, 0, 0]]]])))
    env._feet_sensor_ids = env._feet_body_ids = [0, 1, 2, 3]
    env._leg_policy_indices = torch.arange(12).reshape(4, 3)
    env._physical_forward_axis_b = torch.tensor([[1., 0, 0]])
    env._physical_lateral_axis_b = torch.tensor([[0., 1, 0]])
    env._nominal_foot_lateral_m = torch.tensor([[0.1, -0.1, 0.1, -0.1]])
    env._foot_outward_sign = torch.tensor([[1., -1, 1, -1]])
    env._filtered_actions = torch.full((1, 12), 0.1)
    env._previous_filtered_actions = torch.zeros(1, 12)
    env._action_slew_clamped = torch.zeros(1, 12, dtype=torch.bool)
    env._semantic_vector_b = lambda v: v
    env._get_policy_joint_state = lambda: (torch.zeros(1, 12), torch.zeros(1, 12))
    env._body_posture = lambda: (torch.tensor([0.2]), torch.tensor([0.1]), torch.tensor([0.0]))
    env._posture_commands = torch.zeros(1, 3)
    env._latest_current_validity = torch.ones(1, 12)
    env._evaluation_segments = [("first", 2, 0.1, 0, 0, 0, 0, 0), ("second", 1, 0, 0, 0, 0, 0, 0)]
    env._evaluation_segment_index = -1
    env._evaluation_swing_steps = torch.zeros(4)
    env._evaluation_landings = torch.zeros(4)
    env._evaluation_previous_contact = torch.zeros(4, dtype=torch.bool)
    env._reset_hold_active_mask = torch.tensor([False])
    env._play_step_count = 0
    env.results = []
    env._finish_evaluation_segment = lambda: env.results.append(dict(
        steps=env._evaluation_segment_steps, current=env._evaluation_current_valid_sum,
        roll=env._evaluation_posture_roll_sum, slip=env._evaluation_foot_slip_sum,
        clearance=env._evaluation_swing_foot_clearance_sum,
        forward=env._evaluation_body_forward_sum, action=env._evaluation_action_rate_sum,
    ))
    return env


class EvaluationTests(unittest.TestCase):
    def test_collects_without_calling_rewards_and_keeps_first_and_last_samples(self):
        env = fixture()
        for i in range(4):
            env._play_step_count = i
            terminated, timeout = env._get_dones()
            self.assertTrue(terminated.item())
            self.assertFalse(timeout.item())
        self.assertEqual([r["steps"] for r in env.results], [2, 1])
        self.assertEqual([r["current"] for r in env.results], [2, 1])
        for result in env.results:
            steps = result["steps"]
            self.assertAlmostEqual(result["roll"] / steps, 0.1, places=6)
            self.assertAlmostEqual(result["slip"] / steps, 0.2, places=6)
            self.assertAlmostEqual(result["clearance"] / steps, 0.05, places=6)
            self.assertAlmostEqual(result["forward"] / steps, 0.1, places=6)
            self.assertAlmostEqual(result["action"] / steps, 0.12, places=6)

    def test_reset_hold_and_training_do_not_collect(self):
        env = fixture()
        env._reset_hold_active_mask[:] = True
        env._get_dones()
        self.assertEqual(env._evaluation_segment_index, -1)
        env._reset_hold_active_mask[:] = False
        env._evaluation_segments = ()
        env._get_dones()
        self.assertEqual(env.results, [])
        self.assertEqual(env._evaluation_segment_index, -1)

    def test_all_airborne_or_all_contact_is_finite(self):
        for force in (0.0, 2.0):
            env = fixture()
            env._contact_sensor.data.net_forces_w_history.torch.fill_(force)
            env._get_dones()
            self.assertTrue(torch.isfinite(torch.tensor(env._evaluation_foot_slip_sum)))
            self.assertTrue(torch.isfinite(torch.tensor(env._evaluation_swing_foot_clearance_sum)))


if __name__ == "__main__":
    unittest.main()
