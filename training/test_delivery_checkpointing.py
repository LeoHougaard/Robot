"""CPU round-trip of the real RL-Games critic and its Adam continuation."""
import copy
from pathlib import Path
import unittest

import torch
import yaml
from rl_games.algos_torch.central_value import CentralValueTrain
from rl_games.algos_torch.model_builder import ModelBuilder

from delivery_checkpointing import DeliveryA2CAgent


def make_agent():
    config = yaml.safe_load((Path(__file__).parent / "simple_dog_task_current_body_v20/agents/rl_games_ppo_cfg.yaml").read_text())["params"]["config"]["central_value_config"]
    value = CentralValueTrain(
        state_shape=(436,), value_size=1, ppo_device="cpu", num_agents=1,
        horizon_length=64, num_actors=128, num_actions=12, seq_length=4,
        normalize_value=True, network=ModelBuilder().load(config), config=config,
        writter=None, max_epochs=2000, multi_gpu=False, zero_rnn_on_done=True)
    # Exercise the production save/restore methods without constructing a GPU
    # environment. Only the unrelated actor is a small CPU stand-in.
    agent = DeliveryA2CAgent.__new__(DeliveryA2CAgent)
    agent.model = torch.nn.Linear(426, 12)
    agent.optimizer = torch.optim.Adam(agent.model.parameters(), lr=.0002)
    agent.central_value_net = value
    agent.has_central_value = True
    agent.mixed_precision = agent.normalize_input = agent.normalize_value = agent.normalize_rms_advantage = False
    agent.epoch_num, agent.frame = 5, 40960
    agent.last_lr, agent.entropy_coef = .0002, .005
    agent.last_mean_rewards, agent.vec_env = 0., None
    return agent


def update(value, observations):
    value.train()
    value.optimizer.zero_grad()
    prediction = value(dict(obs=observations, actions=torch.zeros(len(observations), 12),
                            is_train=True, rnn_states=None))["values"]
    loss = (prediction - .4).square().mean()
    loss.backward()
    value.optimizer.step()
    value.epoch_num += 1
    value.frame += len(observations)


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(42)
        torch.set_num_threads(1)

    def test_resumed_next_update_matches_uninterrupted_critic(self):
        original = make_agent()
        batch = torch.randn(128, 436)
        for _ in range(4):
            update(original.central_value_net, batch)
        saved = copy.deepcopy(original.get_full_state_weights())
        restored = make_agent()
        restored.set_full_state_weights(saved)
        self.assertEqual(restored.central_value_net.epoch_num, 4)
        self.assertEqual(restored.central_value_net.frame, 512)
        self.assertTrue(restored.central_value_net.optimizer.state)
        for agent in (original, restored):
            update(agent.central_value_net, batch)
        for key, tensor in original.central_value_net.state_dict().items():
            self.assertTrue(torch.equal(tensor, restored.central_value_net.state_dict()[key]), key)
        self.assertEqual(restored.last_lr, .0002)

    def test_legacy_restart_is_explicit_and_recovers_actor_lr(self):
        original = make_agent()
        weights = copy.deepcopy(original.get_full_state_weights())
        del weights["delivery_central_training"], weights["delivery_actor_schedule"]
        restored = make_agent()
        restored.last_lr = .00015
        with self.assertWarnsRegex(RuntimeWarning, "Adam moments restart"):
            restored.set_full_state_weights(weights)
        self.assertFalse(restored.central_value_net.optimizer.state)
        self.assertEqual(restored.last_lr, .0002)
        self.assertEqual(restored.central_value_net.epoch_num, weights["epoch"])


if __name__ == "__main__":
    unittest.main()
