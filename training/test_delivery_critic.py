"""Check privileged layout and isolation from the deployable actor input."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import torch

tree = ast.parse((Path(__file__).parent / "simple_dog_task_current_body_v20/env.py").read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_critic_observation")
namespace = {"torch": torch}
exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])),
             "delivery_critic", "exec"), namespace)


class DeliveryCriticTests(unittest.TestCase):
    def test_truth_changes_critic_without_changing_actor_or_history(self):
        actor = torch.arange(852, dtype=torch.float32).reshape(2, 426)
        preserved = actor.clone()
        motion = torch.tensor([[.08, -.03, .01], [-.08, .03, -.01]])
        env = NS(_robot=NS(data=NS(root_lin_vel_b=NS(torch=motion))),
                 _semantic_vector_b=lambda value: value,
                 _body_posture=lambda: (torch.tensor([.18, .19]), torch.tensor([.02, -.02]),
                                       torch.tensor([.03, -.03])),
                 _contact_sensor=NS(data=NS(current_contact_time=NS(torch=torch.tensor(
                     [[0., 1., 0., 1.], [1., 0., 1., 0.]])))),
                 _feet_sensor_ids=[3, 1, 0, 2])
        critic = namespace["_critic_observation"](env, actor)
        self.assertEqual(tuple(critic.shape), (2, 436))
        torch.testing.assert_close(critic[:, :426], preserved, rtol=0, atol=0)
        torch.testing.assert_close(critic[:, 426:429], motion, rtol=0, atol=0)
        torch.testing.assert_close(critic[:, 432:], torch.tensor([[1., 1., 0., 0.], [0., 0., 1., 1.]]))
        motion.mul_(-1)
        changed = namespace["_critic_observation"](env, actor)
        self.assertFalse(torch.equal(changed[:, 426:429], critic[:, 426:429]))
        torch.testing.assert_close(actor, preserved, rtol=0, atol=0)
        torch.testing.assert_close(changed[:, :426], preserved, rtol=0, atol=0)


if __name__ == "__main__":
    unittest.main()
