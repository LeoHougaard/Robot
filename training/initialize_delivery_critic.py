"""Add a fresh training-only value network to a preserved V20 initialization.

Actor parameters, observation normalization, optimizer, epoch and frame must
match the existing random epoch-zero baseline exactly. No gait is transferred.
"""
import argparse
import hashlib
import json
from pathlib import Path

import torch
import yaml
from rl_games.algos_torch.central_value import CentralValueTrain
from rl_games.algos_torch.model_builder import ModelBuilder


def initialize(source, config, output):
    if output.exists():
        raise ValueError("refusing to overwrite an initialization")
    checkpoint = torch.load(source, map_location="cpu", weights_only=False)
    if checkpoint["epoch"] != 0 or checkpoint["frame"] != 0 or "assymetric_vf_nets" in checkpoint:
        raise ValueError("requires the preserved epoch-zero V20 baseline without a privileged critic")
    params = yaml.safe_load(config.read_text())["params"]
    cv = params["config"]["central_value_config"]
    torch.manual_seed(42)
    torch.set_num_threads(1)
    value = CentralValueTrain(
        state_shape=(436,), value_size=1, ppo_device="cpu", num_agents=1,
        horizon_length=params["config"]["horizon_length"], num_actors=128, num_actions=12,
        seq_length=params["config"]["seq_length"], normalize_value=True,
        network=ModelBuilder().load(cv), config=cv, writter=None,
        max_epochs=500, multi_gpu=False, zero_rnn_on_done=True,
    )
    checkpoint["assymetric_vf_nets"] = value.state_dict()
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, output)
    saved = torch.load(output, map_location="cpu", weights_only=False)
    original = torch.load(source, map_location="cpu", weights_only=False)
    for key, tensor in original["model"].items():
        if not torch.equal(tensor, saved["model"][key]):
            raise RuntimeError(f"actor/model tensor changed: {key}")
    # Exercise the actual RL-Games critic restore and value path on CPU.
    value.load_state_dict(saved["assymetric_vf_nets"])
    value.eval()
    with torch.no_grad():
        result = value.get_value(dict(states=torch.zeros(128, 436), actions=torch.zeros(128, 12)))
        if result.shape != (128, 1) or not torch.isfinite(result).all():
            raise RuntimeError("invalid restored critic values")
    report = dict(source_checkpoint_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  checkpoint_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                  configuration_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
                  initializer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  actor_model_tensors_identical=True, actor_observations=426, critic_observations=436,
                  critic_seed=42, restored_critic_cpu_cases=128)
    output.with_suffix(".manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    initialize(args.source, args.config, args.output)
