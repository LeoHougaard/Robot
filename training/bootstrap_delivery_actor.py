"""Initialize a new 426-input actor from the exact Pixel V2 ONNX actor.

This is weight initialization, not checkpoint continuation: optimizer, value
network, epoch and reward statistics start fresh. New current/history/posture
columns start at zero weight. The four consecutive V2 frames retain their
normalization and their exact actor function, verified against ONNX's independent
reference evaluator. The source ONNX also passed the Pixel runtime parity test.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import onnx
import torch
import yaml
from onnx.numpy_helper import to_array
from onnx.reference import ReferenceEvaluator
from rl_games.algos_torch.model_builder import ModelBuilder


def bootstrap(actor_path, config_path, output):
    torch.manual_seed(42)
    torch.set_num_threads(1)
    params = yaml.safe_load(config_path.read_text())["params"]
    assert params["network"]["separate"] is True
    assert params["network"]["mlp"]["units"] == [128, 128, 128]
    model = ModelBuilder().load(params).build(dict(
        actions_num=12, input_shape=(426,), num_seqs=1, value_size=1,
        normalize_input=True, normalize_value=True,
    ))
    source = {item.name: to_array(item).copy() for item in onnx.load(str(actor_path)).graph.initializer}
    if source["w0"].shape != (128, 180) or source["obs_mean"].shape != (180,):
        raise ValueError("expected the recorded 180-input V2 actor")
    if not (float(source["norm_min"]) == -5 and float(source["norm_max"]) == 5
            and abs(float(source["epsilon"]) - 1e-5) < 1e-10):
        raise ValueError("source normalization differs from RL-Games")
    # New selected indices = [0,10,20,21,22,23], with 70 values per frame.
    columns = torch.tensor([70 * slot + i for slot in range(2, 6) for i in range(45)])
    state = model.state_dict()
    state["a2c_network.actor_mlp.0.weight"].zero_()
    state["a2c_network.actor_mlp.0.weight"][:, columns] = torch.from_numpy(source["w0"])
    for layer in range(3):
        if layer:
            state[f"a2c_network.actor_mlp.{2 * layer}.weight"][:] = torch.from_numpy(source[f"w{layer}"])
        state[f"a2c_network.actor_mlp.{2 * layer}.bias"][:] = torch.from_numpy(source[f"b{layer}"])
    state["a2c_network.mu.weight"][:] = torch.from_numpy(source["wout"])
    state["a2c_network.mu.bias"][:] = torch.from_numpy(source["bout"])
    state["running_mean_std.running_mean"].zero_()
    state["running_mean_std.running_var"].fill_(1)
    state["running_mean_std.running_mean"][columns] = torch.from_numpy(source["obs_mean"]).to(state["running_mean_std.running_mean"])
    state["running_mean_std.running_var"][columns] = torch.from_numpy(source["obs_var"]).to(state["running_mean_std.running_var"])
    state["running_mean_std.count"].fill_(10000)
    model.load_state_dict(state)
    model.eval()
    source_model = onnx.load(str(actor_path))
    session = ReferenceEvaluator(source_model)
    random = np.random.default_rng(42)
    maximum_error = 0.
    with torch.no_grad():
        for scale in (0., .1, 1., 5., 20.):
            for _ in range(20):
                obs = random.normal(0, scale, (1, 426)).astype(np.float32)
                old = obs[:, columns.numpy()].copy()
                expected = session.run(None, {source_model.graph.input[0].name: old})[0]
                actual = model(dict(is_train=False, prev_actions=None, obs=torch.from_numpy(obs), rnn_states=None))["mus"].clamp(-1, 1).numpy()
                np.testing.assert_allclose(actual, expected, atol=2e-5, rtol=1e-5)
                maximum_error = max(maximum_error, float(np.max(abs(actual - expected))))
    optimizer = torch.optim.Adam(model.parameters(), lr=params["config"]["learning_rate"], eps=1e-8)
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(dict(model=model.state_dict(), optimizer=optimizer.state_dict(), epoch=0, frame=0,
                    last_mean_rewards=-1e9), output)
    report = dict(source_actor_sha256=hashlib.sha256(actor_path.read_bytes()).hexdigest(),
                  checkpoint_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                  config_sha256=hashlib.sha256(config_path.read_bytes()).hexdigest(),
                  exact_actor_parity_cases=100, maximum_absolute_action_error=maximum_error,
                  selected_history_indices=[0, 10, 20, 21, 22, 23],
                  initialization="V2 actor only; new independent critic and optimizer")
    output.with_suffix(".manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("actor", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    bootstrap(args.actor, args.config, args.output)
