"""Behavioral checks for the experiment's actual reward method on CPU."""
import ast
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import torch

tree = ast.parse((Path(__file__).parent / "simple_dog_task_current_body_v20/env.py").read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_get_rewards")
ns = {"torch": torch}
exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "delivery_reward", "exec"), ns)


def reward(command=(0., 0., 0.), motion=(0., 0., 0.), yaw=0., fall=False, settling=False,
           thresholds=(.03, .05), yaw_variance=.09, support_scale=0., feet_down=4):
    wrapped = lambda x: NS(torch=torch.tensor(x, dtype=torch.float32))
    env = NS(
        _robot=NS(data=NS(root_lin_vel_b=wrapped([motion]), root_ang_vel_b=wrapped([[0., 0., yaw]]))),
        _semantic_vector_b=lambda v: v,
        _commands=torch.tensor([command]), _posture_commands=torch.zeros(1, 3),
        _body_posture=lambda: (torch.tensor([.135]), torch.zeros(1), torch.zeros(1)),
        cfg=NS(nominal_support_height_m=.135, opposite_leg_sync_reward_scale=.25,
               progress_planar_threshold=thresholds[0], progress_yaw_threshold=thresholds[1],
               yaw_tracking_variance=yaw_variance, stationary_contact_penalty_scale=support_scale),
        _actions=torch.zeros(1, 12), _previous_actions=torch.zeros(1, 12),
        _contact_sensor=NS(data=NS(current_air_time=wrapped([[0.] * 4]),
                                  current_contact_time=wrapped([[1.] * feet_down + [0.] * (4-feet_down)]))),
        _feet_sensor_ids=[0, 1, 2, 3], _dense_diagonal_gait_reward=lambda a, c: torch.ones(1),
        _reset_hold_active_mask=torch.tensor([settling]), reset_terminated=torch.tensor([fall]),
        step_dt=.02,
        _episode_sums={key: torch.zeros(1) for key in ("body_tracking", "body_motion_shortfall", "opposite_leg_sync")},
    )
    for name in ("_survival_steps", "_velocity_error_sum", "_world_forward_speed_sum", "_body_lateral_speed_sum",
                 "_heading_error_sum", "_terrain_commanded_distance", "_terrain_tracked_distance"):
        setattr(env, name, torch.zeros(1))
    return ns["_get_rewards"](env).item()


class DeliveryRewardTests(unittest.TestCase):
    def test_stationary_support_distinguishes_all_four_feet_without_rewarding_motion(self):
        stood = reward(support_scale=.25)
        for down in range(4):
            self.assertAlmostEqual(stood-reward(support_scale=.25,feet_down=down), .02*.25*(4-down), places=7)
        self.assertEqual(reward(command=(.04,0.,0.),motion=(.04,0.,0.),support_scale=.25,feet_down=2),
                         reward(command=(.04,0.,0.),motion=(.04,0.,0.),support_scale=0.,feet_down=2))
        self.assertGreater(stood, reward(support_scale=.25, motion=(.03,0.,0.)))

    def test_stronger_heading_tracking_keeps_the_progress_prior_no_rocking_bound(self):
        for target in (-.1,-.02,.02,.1):
            def rate(scale):
                return reward(command=(0.,0.,target),yaw=target*scale,thresholds=(.005,.01),
                              yaw_variance=.01,support_scale=.25)
            self.assertGreater(rate(1.),rate(0.))
            self.assertGreater(rate(1.),rate(1.2))
            for amplitude in (.05,.25,.5,1.,2.):
                self.assertLess(.5*(rate(amplitude)+rate(-amplitude)),rate(0.))
        def forward(yaw, variance):
            return reward(command=(.04,0.,0.),motion=(.04,0.,0.),yaw=yaw,yaw_variance=variance)
        old_cost=forward(0.,.09)-forward(.03,.09)
        new_cost=forward(0.,.01)-forward(.03,.01)
        self.assertAlmostEqual(new_cost,9.*old_cost,delta=1e-7)

    def test_slow_command_stage_rewards_tracking_without_buying_overspeed_or_rocking(self):
        for axis, targets in ((0, (-.04, -.01, .01, .04)), (1, (-.02, -.01, .01, .02)),
                              (2, (-.1, -.02, .02, .1))):
            for target in targets:
                command = [0., 0., 0.]
                command[axis] = target
                def rate(scale):
                    return reward(command=command, motion=(scale*command[0], scale*command[1], 0.),
                                  yaw=scale*command[2], thresholds=(.005, .01))
                self.assertGreater(rate(1.), rate(0.), (axis, target))
                self.assertGreater(rate(1.), rate(1.2), (axis, target))
                self.assertLess(.5*(rate(1.)+rate(-1.)), rate(0.), (axis, target))

    def test_stand_is_positive_and_tracks_every_zero_axis(self):
        self.assertGreater(reward(), 0.)
        self.assertGreater(reward(), reward(motion=(.1, 0., 0.)))
        self.assertGreater(reward(), reward(yaw=.3))

    def test_signed_tracking_beats_parking_and_wrong_direction(self):
        for sign in (-1., 1.):
            command = (.15 * sign, 0., 0.)
            tracked = reward(command=command, motion=command)
            parked = reward(command=command)
            wrong = reward(command=command, motion=(-command[0], 0., 0.))
            self.assertGreater(tracked, parked)
            self.assertGreater(parked, wrong)
            self.assertGreater(tracked, reward(command=command, motion=command, yaw=.3))

    def test_fall_is_costly_and_settling_has_no_reward(self):
        self.assertLess(reward(fall=True), -4.)
        self.assertEqual(reward(settling=True), 0.)

    def test_zero_net_translation_cannot_buy_reward_by_rocking(self):
        for axis in (0, 1):
            for target in (-.08, -.04, .04, .08):
                command = [0., 0., 0.]
                command[axis] = target
                parked = reward(command=command)
                for amplitude in (.001, .005, .02, .04, .08, .12):
                    forward = [0., 0., 0.]
                    backward = [0., 0., 0.]
                    forward[axis], backward[axis] = amplitude, -amplitude
                    rocking = .5 * (reward(command=command, motion=forward)
                                    + reward(command=command, motion=backward))
                    self.assertLess(rocking, parked, (command, amplitude))
                opposite = [-3. * value for value in command]
                # Same total forward/backward distance, with a faster return.
                asymmetric = .75 * reward(command=command, motion=command) + .25 * reward(command=command, motion=opposite)
                self.assertLess(asymmetric, parked)

    def test_zero_net_turning_cannot_buy_reward_by_rocking(self):
        for target in (-.2, -.06, .06, .2):
            command = (0., 0., target)
            parked = reward(command=command)
            for amplitude in (.005, .02, .06, .2, .4):
                rocking = .5 * (reward(command=command, yaw=amplitude)
                                + reward(command=command, yaw=-amplitude))
                self.assertLess(rocking, parked, (target, amplitude))
            self.assertLess(.75 * reward(command=command, yaw=target)
                            + .25 * reward(command=command, yaw=-3.*target), parked)
            tracked = reward(command=command, yaw=target)
            self.assertGreater(tracked, reward(command=command, yaw=target*1.2))

    def test_mixed_commands_preserve_the_same_no_rocking_rule(self):
        for command in ((.06, .04, .15), (-.06, .04, -.06), (.04, -.06, .06)):
            def rate(scale):
                return reward(command=command,
                              motion=(scale*command[0], scale*command[1], 0.),
                              yaw=scale*command[2])
            for amplitude in (.05, .25, .5, 1., 2.):
                self.assertLess(.5 * (rate(amplitude) + rate(-amplitude)), rate(0.))
            self.assertLess(.75*rate(1.) + .25*rate(-3.), rate(0.))
            self.assertGreater(rate(1.), rate(1.2))


if __name__ == "__main__":
    unittest.main()
