"""CPU regressions for the real V4 -> Delivery -> Stride observation methods.

Extract only the production methods to avoid starting Isaac. Tensor sensors and
reset machinery below are fixtures; these tests do not validate physics or a
real Isaac lifecycle. The production super() chain and history writes are used.
"""
import ast
import math
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch

import pytest
import torch

from delivery_contract import HISTORY_INDICES, timing_feature_coverage
from deployable_dynamics import gravity_estimate, motor_to_policy, policy_to_motor

ROOT = Path(__file__).parent


class SensorFixtureBase:
    def _reset_idx(self, env_ids):
        self._history_ready[env_ids] = False
        self._current_history_ready[env_ids] = False
        self._observation_history[env_ids] = 0.
        self._current_history[env_ids] = 0.

    def _apply_smooth_commands(self):
        pass


def _load_observation_chain():
    namespace = dict(torch=torch, math=math, SimpleDogCurrentV3Env=SensorFixtureBase,
                     SimpleDogV2Env=SensorFixtureBase, gravity_estimate=gravity_estimate,
                     motor_to_policy=motor_to_policy, policy_to_motor=policy_to_motor,
                     quat_apply_inverse=lambda quaternion, vector: vector)
    for path in ("simple_dog_task_current_body_v4/simple_dog_current_body_v4_env.py",
                 "simple_dog_task_current_body_v20/env.py",
                 "simple_dog_task_current_body_v21/env.py"):
        tree = ast.parse((ROOT / path).read_text())
        classes = [node for node in tree.body if isinstance(node, ast.ClassDef)]
        for cls in classes:
            cls.body = [method for method in cls.body if isinstance(method, ast.FunctionDef)
                        and method.name in ("_get_observations", "_update_timing_history", "_reset_idx")]
        exec(compile(ast.fix_missing_locations(ast.Module(body=classes, type_ignores=[])),
                     str(ROOT / path), "exec"), namespace)
    return namespace


CHAIN = _load_observation_chain()
ENV_CLASSES = [CHAIN[name] for name in ("SimpleDogCurrentBodyV4Env", "DeliveryEnv",
                                      "StrideEnv", "StrideCommandEnv")]


def _env(cls, *, randomized=True, interval=(18., 39.), perturb=False):
    env = cls.__new__(cls)
    env.num_envs, env.device, env.step_dt = 3, "cpu", .02
    env.cfg = NS(domain_randomization_enabled=randomized, timing_interval_ms=interval,
                 timing_feature_perturbation_enabled=perturb,
                 timing_reference_ms=20., observation_history_length=24,
                 observation_frame_size=45, observation_space=426,
                 selected_history_indices=HISTORY_INDICES, observation_noise_enabled=False,
                 reset_small_tilt_deg=0., reset_large_tilt_deg=0., stride_command=(0., 0., 0.))
    wrap = lambda value: NS(torch=value)
    env._robot = NS(data=NS(root_ang_vel_b=wrap(torch.zeros(3, 3)),
                           root_lin_vel_w=wrap(torch.zeros(3, 3)),
                           root_quat_w=wrap(torch.tensor([[1., 0., 0., 0.]]).repeat(3, 1)),
                           projected_gravity_b=wrap(torch.tensor([[0., 0., -1.]]).repeat(3, 1))))
    env._observation_history = torch.zeros(3, 24, 45)
    env._current_history = torch.zeros(3, 24, 24)
    env._timing_history = torch.ones(3, 24, 1)
    env._history_ready = torch.zeros(3, dtype=torch.bool)
    env._current_history_ready = torch.zeros(3, dtype=torch.bool)
    env._sensor_ready = torch.zeros(3, dtype=torch.bool)
    env._commands = torch.zeros(3, 3)
    env._command_targets = torch.zeros(3, 3)
    env._posture_commands = torch.zeros(3, 3)
    env._posture_targets = torch.zeros(3, 3)
    env._actions = torch.zeros(3, 12)
    env._encoder_zeros = torch.zeros(12)
    env._encoder_signs = torch.ones(12)
    env._encoder_previous = torch.zeros(3, 12)
    env._gravity_previous = torch.zeros(3, 3)
    env._world_velocity_previous = torch.zeros(3, 3)
    env._coupled_knees = False
    env._difficulty_fraction = lambda: 1.
    env._semantic_vector_b = lambda value: value
    env._get_policy_joint_state = lambda: (torch.zeros(3, 12), torch.zeros(3, 12))
    env._update_video_camera = lambda: None
    env._simulate_current = lambda: (torch.zeros(3, 12), torch.ones(3, 12))
    env._critic_observation = lambda actor: torch.cat((actor, torch.zeros(3, 10)), dim=-1)
    env._place_resets_on_scheduled_terrain = lambda ids: None
    env._servo_trajectory = NS(reset=lambda *args: None)
    env._stride_elapsed = torch.ones(3)
    env._stride_spec = dict(settle_seconds=2., frequency_hz=.6)
    return env


def _timing(actor):
    return actor[:, :420].reshape(3, 6, 70)[:, :, -1]


@pytest.mark.parametrize("cls", ENV_CLASSES)
@pytest.mark.parametrize("randomized,interval", [(False, (18., 39.)), (True, (20., 20.))])
def test_nominal_actor_timing_is_exactly_one_and_delivery_preserves_rng(cls, randomized, interval):
    env = _env(cls, randomized=randomized, interval=interval)
    rng = torch.random.get_rng_state()
    for _ in range(3):
        actor = env._get_observations()["policy"]
        assert torch.equal(_timing(actor), torch.ones(3, 6))
    if cls.__name__ != "SimpleDogCurrentBodyV4Env":
        assert torch.equal(rng, torch.random.get_rng_state())


@pytest.mark.parametrize("cls", ENV_CLASSES)
def test_stress_reaches_actor_once_per_observation_and_shifts_history(cls):
    torch.manual_seed(173)
    env = _env(cls, perturb=True)
    with patch.object(env, "_update_timing_history", wraps=env._update_timing_history) as update:
        first = env._get_observations()["policy"]
        assert update.call_count == 1
        first_history = env._timing_history.clone()
        assert torch.equal(first_history, first_history[:, -1:, :].expand_as(first_history))
        second = env._get_observations()["policy"]
        assert update.call_count == 2
    assert torch.equal(env._timing_history[:, :-1], first_history[:, 1:])
    assert not torch.equal(env._timing_history[:, -1], first_history[:, -1])
    assert torch.equal(_timing(second), env._timing_history[:, HISTORY_INDICES, 0])
    assert torch.all((_timing(second) >= .9) & (_timing(second) <= 1.95))
    assert not torch.equal(_timing(second), torch.ones(3, 6))
    expected_size = 428 if cls.__name__ in ("StrideEnv", "StrideCommandEnv") else 426
    assert first.shape == second.shape == (3, expected_size)


@pytest.mark.parametrize("cls", ENV_CLASSES)
def test_partial_and_full_reset_do_not_leak_previous_episode_timing(cls):
    env = _env(cls, interval=(39., 39.), perturb=True)
    env._get_observations()
    preserved = env._timing_history[1:].clone()
    env._reset_idx(torch.tensor([0]))
    assert torch.equal(env._timing_history[0], torch.ones(24, 1))
    assert torch.equal(env._timing_history[1:], preserved)
    env.cfg.timing_interval_ms = (18., 18.)
    actor = env._get_observations()["policy"]
    torch.testing.assert_close(env._timing_history[0], torch.full((24, 1), .9))
    assert torch.equal(env._timing_history[1:, :-1], preserved[:, 1:])
    assert torch.equal(_timing(actor), env._timing_history[:, HISTORY_INDICES, 0])
    env._reset_idx(None)
    assert torch.equal(env._timing_history, torch.ones(3, 24, 1))
    env.cfg.domain_randomization_enabled = False
    assert torch.equal(_timing(env._get_observations()["policy"]), torch.ones(3, 6))


def test_delivery_timing_perturbation_does_not_change_other_actor_inputs_or_step_dt():
    nominal, stress = _env(CHAIN["StrideCommandEnv"], randomized=False), _env(CHAIN["StrideCommandEnv"], perturb=True)
    for _ in range(4):
        a, b = nominal._get_observations()["policy"], stress._get_observations()["policy"]
        keep = torch.ones(428, dtype=torch.bool)
        keep[torch.arange(69, 420, 70)] = False
        assert torch.equal(a[:, keep], b[:, keep])
        assert nominal.step_dt == stress.step_dt == .02


def test_v22_robust_and_highlift_use_tested_stride_command_chain():
    tree = ast.parse((ROOT / "simple_dog_task_current_body_v22/__init__.py").read_text())
    registrations = ast.literal_eval(next(node for node in tree.body if isinstance(node, ast.For)).iter)
    for stage in ("Robust", "Sustained", "Rough125", "Stairs", "VariedLift"):
        assert next(row[1] for row in registrations if row[0] == stage) == "StrideCommandEnv"


def test_realized_coverage_rejects_the_original_constant_ones_bug():
    with pytest.raises(RuntimeError, match="did not vary"):
        timing_feature_coverage(1., 1., 1000, interval_ms=(18., 39.), reference_ms=20., randomized=True)
    report = timing_feature_coverage(.91, 1.94, 1000, interval_ms=(18., 39.), reference_ms=20., randomized=True)
    assert report["varied"]
    assert report["variable_sensor_sampling"] is False
    assert report["variable_action_hold_physics"] is False


@pytest.mark.parametrize("low,high,count", [(float("nan"), 1., 20), (1., float("inf"), 20),
                                           (1., 1., 0), (.8, 1.9, 20), (1., 2., 20)])
def test_realized_coverage_rejects_missing_nonfinite_and_out_of_range_samples(low, high, count):
    with pytest.raises(RuntimeError):
        timing_feature_coverage(low, high, count, interval_ms=(18., 39.), reference_ms=20., randomized=True)


def test_realized_nominal_coverage_is_not_mislabeled_as_stress():
    report = timing_feature_coverage(1., 1., 1000, interval_ms=(18., 39.), reference_ms=20., randomized=False)
    assert report["varied"] is False


@pytest.mark.parametrize("cls", ENV_CLASSES[1:])
def test_historical_delivery_presets_keep_fixed_timing_despite_inherited_stress_bounds(cls):
    env = _env(cls, randomized=True, interval=(18., 39.))
    rng = torch.random.get_rng_state()
    for _ in range(3):
        assert torch.equal(_timing(env._get_observations()["policy"]), torch.ones(3, 6))
    assert torch.equal(rng, torch.random.get_rng_state())


def test_delivery_feature_perturbation_defaults_off_in_real_configs():
    tree = ast.parse((ROOT / "simple_dog_task_current_body_v20/env_cfg.py").read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "DeliveryTrainCfg")
    setting = next(node.value for node in cls.body if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "timing_feature_perturbation_enabled"
                           for target in node.targets))
    assert ast.literal_eval(setting) is False
    for path in ("simple_dog_task_current_body_v21/env_cfg.py", "simple_dog_task_current_body_v22/env_cfg.py"):
        tree = ast.parse((ROOT / path).read_text())
        assert not any(isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Name) and target.id == "timing_feature_perturbation_enabled"
                               for target in node.targets) for node in ast.walk(tree))


def test_v4_retains_original_timing_rng_sequence():
    for randomized in (False, True):
        env = _env(CHAIN["SimpleDogCurrentBodyV4Env"], randomized=randomized)
        torch.manual_seed(973)
        expected = [torch.empty(3, 1).uniform_(18., 39.) / 20. for _ in range(2)]
        expected_rng = torch.random.get_rng_state()
        torch.manual_seed(973)
        for sample in expected:
            env._get_observations()
            torch.testing.assert_close(env._timing_history[:, -1], sample if randomized else torch.ones(3, 1))
        assert torch.equal(expected_rng, torch.random.get_rng_state())
