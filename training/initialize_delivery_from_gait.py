"""Initialize the unchanged physical actor from measured simulation strides.

This is supervised initialization, not a walking acceptance result. A small
temporal holdout detects fitting failures; only a closed-loop rollout can
establish whether the actor sustains the reference motion itself.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path

import torch

from bootstrap_delivery_actor import new_model


def initialize(dataset_path, source, config, output, updates=1500):
    if output.exists() or output.with_suffix(".manifest.json").exists():
        raise ValueError("refusing to overwrite initialization evidence")
    checkpoint = torch.load(source, map_location="cpu", weights_only=False)
    if checkpoint["epoch"] != 0 or checkpoint["frame"] != 0:
        raise ValueError("requires the preserved random epoch-zero baseline")
    data = torch.load(dataset_path, map_location="cpu", weights_only=False)
    observations, actions = data["observations"], data["actions"]
    if observations.shape != (900, len(data["settings"]), 426) or actions.shape != (*observations.shape[:2], 12):
        raise ValueError("unexpected demonstration shape or actor contract")
    if data["dt"] != .02 or not torch.isfinite(observations).all() or not torch.isfinite(actions).all():
        raise ValueError("nonfinite demonstrations or incorrect sample rate")
    # The first sweep found pre-limit clipping in sideways and yaw reference
    # targets. Start this acquisition diagnostic with the unclipped straight
    # strides. The delivery evaluation still includes every original command.
    names = {"stand", "forward_slow", "reverse_slow"}
    selected = [i for i, setting in enumerate(data["settings"])
                if setting["name"] in names and setting["frequency"] == .6]
    if len(selected) != len(names):
        raise ValueError("requires one reviewed slow reference for each initial command")
    for index in selected:
        result = data["results"][index]
        if result["resets"] or result["requested_joint_clip_fraction"] > .001 or result["mean_tilt_rad"] > .12:
            raise ValueError(f"invalid reference case: {result['name']}")
        if result["name"] != "stand" and min(result["landings_frflbrbl"]) < 4:
            raise ValueError("reference must complete multiple swings on every foot")
    # Whole late cycles, with a two-second gap. This is a fitting diagnostic,
    # not an independent terrain, seed or physical transfer test.
    x = observations[:550, selected].flatten(0, 1)
    y = actions[:550, selected].flatten(0, 1)
    holdout_x = observations[650:, selected].flatten(0, 1)
    holdout_y = actions[650:, selected].flatten(0, 1)
    params, model = new_model(config)
    model.load_state_dict(checkpoint["model"], strict=True)
    state = model.state_dict()
    state["running_mean_std.running_mean"].copy_(x.mean(0))
    state["running_mean_std.running_var"].copy_(x.var(0, unbiased=False).clamp_min(1e-4))
    state["running_mean_std.count"].fill_(len(x))
    model.load_state_dict(state)
    model.eval()  # Fix normalization; only actor means learn during fitting.
    actor = []
    for name, parameter in model.named_parameters():
        trainable = name.startswith(("a2c_network.actor_mlp.", "a2c_network.mu."))
        parameter.requires_grad_(trainable)
        if trainable:
            actor.append(parameter)
    optimizer = torch.optim.Adam(actor, lr=3e-4)

    def predict(batch):
        return model(dict(is_train=False, prev_actions=None, obs=batch, rnn_states=None))["mus"]

    best_loss, best_state, best_update = float("inf"), None, 0
    trace = []
    for update in range(updates + 1):
        if update % 100 == 0:
            with torch.no_grad():
                error = predict(holdout_x) - holdout_y
                loss = float(error.square().mean())
                entry = dict(update=update, holdout_action_rmse=loss ** .5,
                             holdout_action_p99=float(torch.quantile(error.abs(), .99)))
                trace.append(entry)
                print(json.dumps(entry), flush=True)
                if loss < best_loss:
                    best_loss, best_state, best_update = loss, copy.deepcopy(model.state_dict()), update
        if update == updates:
            break
        indices = torch.randint(len(x), (512,))
        loss = (predict(x[indices]) - y[indices]).square().mean()
        if not torch.isfinite(loss):
            raise RuntimeError("nonfinite supervised fitting loss")
        optimizer.zero_grad(set_to_none=True)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(actor, 1.)
        optimizer.step()
    if best_state is None or best_loss ** .5 > .1:
        raise RuntimeError("reference fitting failed; no initializer written")
    model.load_state_dict(best_state)
    for key, tensor in checkpoint["model"].items():
        if not key.startswith(("a2c_network.actor_mlp.", "a2c_network.mu.", "running_mean_std.")):
            if not torch.equal(tensor, best_state[key]):
                raise RuntimeError(f"non-actor state changed during fitting: {key}")
    # The fitting optimizer is not the PPO optimizer. PPO and both critics
    # start at epoch zero, with the preserved random value-network weights.
    checkpoint["model"] = best_state
    for parameter in model.parameters():
        parameter.requires_grad_(True)
    checkpoint["optimizer"] = torch.optim.Adam(
        model.parameters(), lr=params["config"]["learning_rate"], eps=1e-8).state_dict()
    output.parent.mkdir(parents=True, exist_ok=True)
    torch.save(checkpoint, output)
    restored = torch.load(output, map_location="cpu", weights_only=False)
    model.load_state_dict(restored["model"], strict=True)
    if restored["epoch"] != 0 or restored["frame"] != 0:
        raise RuntimeError("initializer is incorrectly marked as PPO training")
    report = dict(initialization="supervised causal sensor-to-target fit; not promoted",
                  best_update=best_update, holdout_action_rmse=best_loss ** .5,
                  holdout_limitation="late cycles of the same trajectories, not independent generalization",
                  cases=[data["settings"][i] for i in selected], training_samples=len(x),
                  holdout_samples=len(holdout_x), trace=trace, actor_observations=426,
                  checkpoint_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                  source_checkpoint_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  dataset_sha256=hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                  config_sha256=hashlib.sha256(config.read_bytes()).hexdigest(),
                  initializer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    output.with_suffix(".manifest.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "trace"}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("config", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    initialize(args.dataset, args.source, args.config, args.output)
