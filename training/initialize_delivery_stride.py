"""Preserve and verify an epoch-zero V21 residual actor and full critic state."""
import argparse
import hashlib
import json
from pathlib import Path

import torch
import yaml
from rl_games.algos_torch.central_value import CentralValueTrain
from rl_games.algos_torch.model_builder import ModelBuilder


def build_actor(config):
    params = yaml.safe_load(config.read_text())["params"]
    model = ModelBuilder().load(params).build(dict(
        actions_num=12, input_shape=(428,), num_seqs=1, value_size=1,
        normalize_input=True, normalize_value=True))
    return params, model


def initialize(config, output):
    if output.exists():
        raise ValueError("refusing to overwrite an initialization")
    torch.manual_seed(42)
    torch.set_num_threads(1)
    params, actor = build_actor(config)
    actor.eval()
    with torch.no_grad():
        for scale in (0., .1, 1., 10.):
            result = actor(dict(is_train=False, prev_actions=None,
                                obs=scale * torch.randn(128, 428), rnn_states=None))
            if not torch.equal(result["mus"], torch.zeros_like(result["mus"])):
                raise RuntimeError("initial actor does not exactly preserve the reference")
    std = actor.a2c_network.sigma.detach().exp()
    if not torch.allclose(std, torch.full_like(std, .2)):
        raise RuntimeError("initial residual exploration differs from its measured magnitude")
    cv = params["config"]["central_value_config"]
    critic = CentralValueTrain(
        state_shape=(438,), value_size=1, ppo_device="cpu", num_agents=1,
        horizon_length=params["config"]["horizon_length"], num_actors=128, num_actions=12,
        seq_length=params["config"]["seq_length"], normalize_value=True,
        network=ModelBuilder().load(cv), config=cv, writter=None,
        max_epochs=params["config"]["max_epochs"], multi_gpu=False, zero_rnn_on_done=True)
    optimizer = torch.optim.Adam(actor.parameters(), lr=params["config"]["learning_rate"], eps=1e-8)
    checkpoint = dict(model=actor.state_dict(), optimizer=optimizer.state_dict(), epoch=0, frame=0,
                      last_mean_rewards=-1e9, assymetric_vf_nets=critic.state_dict(),
                      delivery_actor_schedule=dict(last_lr=params["config"]["learning_rate"], entropy_coef=0.),
                      delivery_central_training=dict(optimizer=critic.optimizer.state_dict(),
                                                     epoch=critic.epoch_num, frame=critic.frame, lr=critic.lr),
                      policy_family="current_body_v21", action_contract="stride_reference_v1")
    contract = params["config"].get("delivery_policy_contract")
    if contract is not None:
        checkpoint.update(delivery_policy_contract=contract,
                          policy_family=contract["policy_family"], action_contract="stride_reference_cad_v1")
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, output)
    saved = torch.load(output, weights_only=False, map_location="cpu")
    actor.load_state_dict(saved["model"], strict=True)
    critic.load_state_dict(saved["assymetric_vf_nets"], strict=True)
    critic.eval()
    with torch.no_grad():
        value = critic.get_value(dict(states=torch.zeros(128, 438), actions=torch.zeros(128, 12)))
        assert value.shape == (128, 1) and torch.isfinite(value).all()
    report = dict(checkpoint_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                  config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
                  initializer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  zero_residual_cases=512, actor_inputs=428, critic_inputs=438, std=std.tolist(),
                  seed=42, initialization="zero residual head, random feature layers and critic, fresh optimizers")
    output.with_suffix(".manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    initialize(args.config, args.output)
