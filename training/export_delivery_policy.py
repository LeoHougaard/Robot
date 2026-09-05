"""Export V20 only after matched numeric, video and original-collision checks."""
import argparse
import hashlib
import json
from pathlib import Path

from delivery_contract import BUILDER, HISTORY_INDICES
from delivery_provenance import sha, validate


def checked_actor(checkpoint, agent_path):
    """Check the exact trained function without writing a deployable bundle."""
    import numpy as np
    import torch
    import yaml
    from rl_games.algos_torch.model_builder import ModelBuilder
    from export_v2_policy import TENSOR_KEYS, numpy_actor

    params = yaml.safe_load(agent_path.read_text())["params"]
    state = torch.load(checkpoint, map_location="cpu", weights_only=False)
    model = ModelBuilder().load(params).build(dict(actions_num=12, input_shape=(426,), num_seqs=1,
                                                   value_size=1, normalize_input=True, normalize_value=True))
    model.load_state_dict(state["model"], strict=True)
    model.eval()
    arrays = {name: state["model"][key].detach().float().numpy() for name, key in TENSOR_KEYS.items()}
    if any(not np.isfinite(value).all() for value in arrays.values()) or (arrays["obs_var"] < 0).any():
        raise ValueError("non-finite weights or invalid normalization")
    random = np.random.default_rng(2042)
    maximum_error = 0.
    with torch.no_grad():
        for scale in (0., .1, 1., 5., 20.):
            for _ in range(20):
                observation = random.normal(0, scale, 426).astype(np.float32)
                actual = numpy_actor(observation, arrays)
                expected = model(dict(is_train=False, prev_actions=None, obs=torch.from_numpy(observation[None]),
                                      rnn_states=None))["mus"][0].clamp(-1, 1).numpy()
                np.testing.assert_allclose(actual, expected, atol=2e-5, rtol=1e-5)
                maximum_error = max(maximum_error, float(abs(actual-expected).max()))
    return state, arrays, maximum_error


def export(checkpoint, output, profile_path, fit_path, evaluations, fidelity):
    import numpy as np
    import yaml
    from current_policy_fit import load_current_policy_fit

    profile = json.loads(profile_path.read_text())
    profile_sha = hashlib.sha256(json.dumps(profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    checkpoint_sha, fit_sha = sha(checkpoint), sha(fit_path)
    evidence = {suite: validate(path, checkpoint_sha, profile_sha, fit_sha, suite)
                for suite, path in evaluations.items()}
    if set(evidence) != {"deliveryflat", "delivery", "deliverystress"}:
        raise ValueError("flat, mild terrain and stress evidence are all required")
    sources = [item["provenance"]["source_files"] for item in evidence.values()]
    if any(item != sources[0] for item in sources[1:]):
        raise ValueError("evaluation suites used different source revisions")
    if len({item["provenance"]["seed"] for item in evidence.values()}) != 3:
        raise ValueError("use distinct held-out seeds for the three suites")
    fidelity_profile = json.loads((fidelity.parent / "control_profile.json").read_text())
    conversion = evidence["deliveryflat"]["provenance"]["asset"]["conversion_manifest"]
    if fidelity_profile["robot"]["asset_usd"] != conversion["source"]:
        raise ValueError("fidelity evaluation must use the preserved original SDF asset")
    original_fidelity_profile = json.loads(json.dumps(fidelity_profile))
    original_fidelity_profile["robot"]["asset_usd"] = profile["robot"]["asset_usd"]
    if original_fidelity_profile != profile:
        raise ValueError("fidelity comparison changed more than collision representation")
    fidelity_sha = hashlib.sha256(json.dumps(fidelity_profile, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    fidelity_result = validate(fidelity, checkpoint_sha, fidelity_sha, fit_sha, "deliveryflat")
    if fidelity_result["provenance"]["source_files"] != sources[0]:
        raise ValueError("fidelity comparison used different evaluation source")
    if fidelity_result["provenance"]["seed"] != evidence["deliveryflat"]["provenance"]["seed"]:
        raise ValueError("fidelity comparison must use the flat evaluation seed")

    flat_root = evaluations["deliveryflat"].parent
    cfg = yaml.load((flat_root / "resolved_env.yaml").read_text(), Loader=yaml.BaseLoader)
    if (int(cfg["control_hz"]) != 50 or list(map(int, cfg["selected_history_indices"])) != list(HISTORY_INDICES)
            or float(cfg["stationary_planar_deadband"]) >= 0 or float(cfg["stationary_yaw_deadband"]) >= 0):
        raise ValueError("evaluated environment differs from the V20 runtime contract")
    state, arrays, maximum_error = checked_actor(
        checkpoint, flat_root / "source/simple_dog_task_current_body_v20/agents/rl_games_ppo_cfg.yaml")
    fit = load_current_policy_fit(fit_path, [joint["semantic"] for joint in profile["robot"]["joints"]], control_hz=50)
    # Create only after every gate and actor parity pass; never overwrite a bundle.
    output.mkdir(parents=True, exist_ok=False)
    np.savez_compressed(output / "policy_weights.npz", **arrays)
    metadata = dict(
        schema_version=5, deployment_tier="delivery_v20_simulation_verified", profile_id=profile["profile_id"],
        profile_sha256=profile_sha, checkpoint_sha256=checkpoint_sha, checkpoint_epoch=int(state["epoch"]),
        weights_sha256=sha(output / "policy_weights.npz"), observation_size=426, observation_history=24,
        observation_builder=BUILDER, action_size=12, control_hz=50,
        command_smoothing_time_s=float(cfg["command_smoothing_time_s"]),
        history_selection=dict(frame_size=70, indices=list(HISTORY_INDICES), timing_reference_ms=20.),
        action_contract=dict(actor_output_clip=[-1., 1.],
                             applied_normalized_clip_by_joint=list(map(float, cfg["action_limit_by_joint"])),
                             low_pass_alpha=float(cfg["action_filter_alpha"]),
                             applied_normalized_slew_limit=float(cfg["action_delta_limit"]),
                             position_target_scale_rad=float(cfg["action_scale"])),
        stationary_action_contract=dict(behavior="policy_stabilization", planar_command_deadband_m_s=0.,
                                        yaw_command_deadband_rad_s=0., normalized_stance_action=[0.]*12),
        validated_command_limits=dict(forward_m_s=[-.08, .08], lateral_m_s=[-.06, .06], yaw_rate_rad_s=[-.20, .20]),
        posture_command_contract=dict(height_offset_m=[-.01, .01], roll_rad=[-.06, .06], pitch_rad=[-.06, .06],
                                      smoothing_time_s=float(cfg["posture_smoothing_time_s"]), layout="append_after_selected_history"),
        current_observation_contract=dict(units="mA", absolute=True, normalization_bias_ma=list(fit.current_bias_ma),
                                          normalization_scale_ma=list(fit.current_scale_ma),
                                          clip_normalized=[c/s for c,s in zip(fit.current_clip_ma,fit.current_scale_ma)],
                                          current_step_ma=6.5, missing_behavior="hold_last_finite_and_validity_zero"),
        hardware_requirements=dict(firmware_minimum="0.1.14", baud=2000000, feedback_period_ms=20,
                                   servo_speed_steps_s=1200, servo_acceleration_register=30),
        servo_response_fit_sha256=sha(flat_root / "source/fits/servo-response-20260829.json"),
        activation="elu", normalization_epsilon=1e-5, normalization_clip=[-5., 5.], physical_fit=fit.metadata(),
        evaluation=evidence["delivery"], evaluation_set={**evidence, "original_collision": fidelity_result},
        actor_parity=dict(cases=100, maximum_absolute_error=maximum_error), physical_walking_verified=False,
    )
    (output / "policy_metadata.json").write_text(json.dumps(metadata, indent=2, allow_nan=False) + "\n")
    print(json.dumps(dict(bundle=str(output), checkpoint_sha256=checkpoint_sha, parity_error=maximum_error)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--fit", type=Path, required=True)
    for name in ("flat", "mild", "stress", "fidelity"):
        parser.add_argument(f"--{name}-evaluation", type=Path, required=True)
    args = parser.parse_args()
    export(args.checkpoint, args.output, args.profile, args.fit,
           {"deliveryflat": args.flat_evaluation, "delivery": args.mild_evaluation, "deliverystress": args.stress_evaluation},
           args.fidelity_evaluation)
