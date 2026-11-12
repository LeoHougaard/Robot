"""Behavioral checks for the experiment's actual reward method on CPU."""
import ast
import hashlib
import os
import subprocess
import textwrap
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace as NS
import unittest
import torch

tree = ast.parse((Path(__file__).parent / "simple_dog_task_current_body_v20/env.py").read_text())
cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_get_rewards")
ns = {"torch": torch}
exec(compile(ast.fix_missing_locations(ast.Module(body=[method], type_ignores=[])), "delivery_reward", "exec"), ns)

dense_tree = ast.parse((Path(__file__).parent / "simple_dog_task_v2/simple_dog_v2_env.py").read_text())
dense_cls = next(n for n in dense_tree.body if isinstance(n, ast.ClassDef) and n.name == "SimpleDogV2Env")
dense_method = next(n for n in dense_cls.body if isinstance(n, ast.FunctionDef) and n.name == "_dense_diagonal_gait_reward")
dense_ns = {"torch": torch}
exec(compile(ast.fix_missing_locations(ast.Module(body=[dense_method], type_ignores=[])), "dense_reward", "exec"), dense_ns)
command_cfg = ast.parse((Path(__file__).parent / "simple_dog_task_current_body_v22/env_cfg.py").read_text())
command_class = next(n for n in command_cfg.body if isinstance(n, ast.ClassDef) and n.name == "CadStrideCommandsCfg")
EXPERIMENT_SCALE = ast.literal_eval(next(n.value for n in command_class.body if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "moving_foot_duration_penalty_scale" for t in n.targets)))


@lru_cache(maxsize=1)
def legacy_reward_method():
    try:
        source = subprocess.check_output(
            ["git", "show", "41677bd:training/simple_dog_task_current_body_v20/env.py"],
            cwd=Path(__file__).parent, stderr=subprocess.DEVNULL, text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        source = Path(os.environ["LEGACY_DELIVERY_REWARD_SOURCE"]).read_text()
    assert hashlib.sha256(source.replace('\r\n', '\n').encode()).hexdigest() == (
        "8cc8d3e6f62ed47e1f49299738ed698702259bebd8626c67ea7963d2b126c3af"
    ), "legacy reward source must be the exact preserved 41677bd file"
    old_tree = ast.parse(textwrap.dedent(source))
    old_cls = next((n for n in old_tree.body if isinstance(n, ast.ClassDef) and n.name == "DeliveryEnv"), None)
    old_method = next((n for n in (old_cls.body if old_cls else old_tree.body)
                       if isinstance(n, ast.FunctionDef) and n.name == "_get_rewards"), None)
    old_ns = {"torch": torch}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[old_method], type_ignores=[])), "legacy_reward", "exec"), old_ns)
    return old_ns["_get_rewards"]


def reward(command=(0., 0., 0.), motion=(0., 0., 0.), yaw=0., fall=False, settling=False,
           thresholds=(.03, .05), yaw_variance=.09, support_scale=0., feet_down=4,
           contact_times=None, air_times=None, duration_scale=0., use_dense=True,
           method_name="new"):
    wrapped = lambda x: NS(torch=torch.tensor(x, dtype=torch.float32))
    env = NS(
        _robot=NS(data=NS(root_lin_vel_b=wrapped([motion]), root_ang_vel_b=wrapped([[0., 0., yaw]]))),
        _semantic_vector_b=lambda v: v,
        _commands=torch.tensor([command]), _posture_commands=torch.zeros(1, 3),
        _body_posture=lambda: (torch.tensor([.135]), torch.zeros(1), torch.zeros(1)),
        cfg=NS(nominal_support_height_m=.135, opposite_leg_sync_reward_scale=.25,
               progress_planar_threshold=thresholds[0], progress_yaw_threshold=thresholds[1],
               yaw_tracking_variance=yaw_variance, stationary_contact_penalty_scale=support_scale,
               moving_foot_duration_penalty_scale=duration_scale,
               moving_foot_contact_limit_s=1.25, moving_foot_air_limit_s=1.0,
               moving_foot_duration_ramp_s=.25),
        _actions=torch.zeros(1, 12), _previous_actions=torch.zeros(1, 12),
        _contact_sensor=NS(data=NS(current_air_time=wrapped([air_times or [0.] * 4]),
                                  current_contact_time=wrapped([contact_times or ([1.] * feet_down + [0.] * (4-feet_down))]))),
        _feet_sensor_ids=[0, 1, 2, 3],
        _dense_diagonal_gait_reward=lambda a, c: dense_ns["_dense_diagonal_gait_reward"](
            NS(cfg=NS(diagonal_gait_std=.10)), a, c) if use_dense else torch.ones(1),
        _reset_hold_active_mask=torch.tensor([settling]), reset_terminated=torch.tensor([fall]),
        step_dt=.02,
        _episode_sums={key: torch.zeros(1) for key in ("body_tracking", "body_motion_shortfall", "opposite_leg_sync")},
    )
    for name in ("_survival_steps", "_velocity_error_sum", "_world_forward_speed_sum", "_body_lateral_speed_sum",
                 "_heading_error_sum", "_terrain_commanded_distance", "_terrain_tracked_distance"):
        setattr(env, name, torch.zeros(1))
    method = legacy_reward_method() if method_name == "legacy" else ns["_get_rewards"]
    return method(env).item()


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

    def test_moving_duration_cost_ramps_caps_and_preserves_stop(self):
        kwargs = dict(command=(.04, 0., 0.), motion=(.04, 0., 0.), duration_scale=.25, use_dense=False)
        below = reward(**kwargs, contact_times=[1.25, 1., 1., 1.])
        half = reward(**kwargs, contact_times=[1.375, 1., 1., 1.])
        capped = reward(**kwargs, contact_times=[3., 1., 1., 1.])
        self.assertGreater(below, half)
        self.assertGreater(half, capped)
        self.assertAlmostEqual(capped, reward(**kwargs, contact_times=[9., 1., 1., 1.]), places=7)
        stop = reward(support_scale=.25, duration_scale=.25, contact_times=[3., 0., 0., 0.])
        legacy = reward(support_scale=.25, duration_scale=0., contact_times=[3., 0., 0., 0.])
        self.assertEqual(stop, legacy)

    def test_scale_zero_matches_preserved_legacy_reward(self):
        cases = [
            dict(command=(.04, 0., 0.), motion=(.04, .002, 0.), contact_times=[2., .8, .8, .8]),
            dict(command=(0., .02, 0.), motion=(0., .016, 0.), air_times=[1.2, .2, .2, .2]),
            dict(command=(0., 0., .1), yaw=.087, contact_times=[.8, .8, .8, .8]),
            dict(command=(0., 0., 0.), contact_times=[3., 0., 0., 0.], support_scale=.25),
        ]
        for case in cases:
            self.assertAlmostEqual(reward(**case, duration_scale=0.),
                                   reward(**case, duration_scale=0., method_name="legacy"), places=7)

    def test_physical_diagonal_cycles_beat_one_or_two_planted_feet_at_equal_tracking(self):
        # Evaluate full cycles, not simultaneous air/contact values. FR/BL and
        # FL/BR are the diagonal pairs, at the reference's 0.6 Hz and 60% duty.
        def average(planted):
            samples = []
            for step in range(300, 550):
                elapsed = step * .02
                air, contact = [], []
                for foot, offset in enumerate((0., .5, .5, 0.)):
                    phase = (elapsed * .6 + offset) % 1.
                    air.append(0. if foot in planted or phase < .6 else (phase-.6)/.6)
                    contact.append(elapsed if foot in planted else phase/.6 if phase < .6 else 0.)
                    self.assertFalse(air[-1] > 0 and contact[-1] > 0)
                samples.append(reward(command=(0., .02, 0.), motion=(0., .02, 0.),
                    thresholds=(.005, .01), yaw_variance=.01, duration_scale=EXPERIMENT_SCALE,
                    air_times=air, contact_times=contact))
            return sum(samples)/len(samples)
        cycling = average(set())
        self.assertGreater(cycling, average({0}))
        self.assertGreater(cycling, average({1, 2}))

    def test_prolonged_air_ramps_caps_and_is_suppressed_during_reset(self):
        kwargs = dict(command=(.04, 0., 0.), motion=(.04, 0., 0.), duration_scale=.25, use_dense=False)
        below = reward(**kwargs, air_times=[1., 0., 0., 0.])
        half = reward(**kwargs, air_times=[1.125, 0., 0., 0.])
        capped = reward(**kwargs, air_times=[2., 0., 0., 0.])
        self.assertGreater(below, half)
        self.assertGreater(half, capped)
        self.assertAlmostEqual(capped, reward(**kwargs, air_times=[9., 0., 0., 0.]), places=7)
        self.assertEqual(reward(**kwargs, air_times=[9., 0., 0., 0.], settling=True), 0.)

    def test_measured_rate_comparisons_do_not_reward_wrong_way_or_parking(self):
        # Simplified measured command rates from the preserved 250/750 screens.
        for command, measured in [((.04, 0., 0.), .042), ((0., .02, 0.), .016), ((0., 0., .1), .087)]:
            tracked = reward(command=command, motion=(measured if command[0] else 0., measured if command[1] else 0., 0.),
                             yaw=measured if command[2] else 0., duration_scale=.25,
                             contact_times=[.8, .8, .8, .8])
            parked = reward(command=command, duration_scale=.25, contact_times=[2., 2., 2., 2.])
            wrong = reward(command=command, motion=(-command[0], -command[1], 0.), yaw=-command[2],
                           duration_scale=.25, contact_times=[2., 2., 2., 2.])
            self.assertGreater(tracked, parked)
            self.assertGreater(parked, wrong)

    def test_measured_reward_delta_requires_more_than_quarter_scale_for_planted_yaw(self):
        # Exact mean rewards from the preserved old/new-source command replay.
        # The actor and physical trajectories are unchanged. Only this linear
        # penalty changes; these values do not predict a retrained policy.
        cases = (
            # 250 cycling, 750 planted foot, and their penalty deltas at .25.
            (.09750978648662567, .10124178230762482, -.000048741698265075684, -.0038359835743904114),
            (.09101299941539764, .10203352570533752, -.00019478797912597656, -.007374942302703857),
        )
        for old250, old750, delta250, delta750 in cases:
            self.assertGreater(old750, old250)
            self.assertGreater(old250 + EXPERIMENT_SCALE/.25*delta250,
                               old750 + EXPERIMENT_SCALE/.25*delta750)
        old250, old750, delta250, delta750 = cases[1]
        self.assertGreater(old750 + delta750, old250 + delta250)

    def test_moving_duration_cost_prefers_cycle_without_step_in_place_bonus(self):
        tracked = reward(command=(.04, 0., 0.), motion=(.04, 0., 0.), duration_scale=.25,
                         contact_times=[.8, .8, .8, .8])
        planted = reward(command=(.04, 0., 0.), motion=(.04, 0., 0.), duration_scale=.25,
                         contact_times=[2., 2., 2., 2.])
        self.assertGreater(tracked, planted)
        stepping = reward(command=(.04, 0., 0.), motion=(0., 0., 0.), duration_scale=.25,
                          contact_times=[.8, .8, .8, .8])
        self.assertLess(stepping, tracked)

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
