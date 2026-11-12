"""Export a deterministic RL-Games V2 actor to a portable NumPy bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


TENSOR_KEYS = {
    "obs_mean": "running_mean_std.running_mean",
    "obs_var": "running_mean_std.running_var",
    "w0": "a2c_network.actor_mlp.0.weight",
    "b0": "a2c_network.actor_mlp.0.bias",
    "w1": "a2c_network.actor_mlp.2.weight",
    "b1": "a2c_network.actor_mlp.2.bias",
    "w2": "a2c_network.actor_mlp.4.weight",
    "b2": "a2c_network.actor_mlp.4.bias",
    "wout": "a2c_network.mu.weight",
    "bout": "a2c_network.mu.bias",
}

REQUIRED_GOAL_COMMANDS = {
    "stand": (0.0, 0.0, 0.0),
    "forward": (0.22, 0.0, 0.0),
    "reverse": (-0.18, 0.0, 0.0),
    "strafe_left": (0.0, 0.16, 0.0),
    "strafe_right": (0.0, -0.16, 0.0),
    "turn_left": (0.0, 0.0, 0.25),
    "turn_right": (0.0, 0.0, -0.25),
    "diagonal_left": (0.16, 0.12, 0.0),
    "diagonal_right": (0.16, -0.12, 0.0),
    "diagonal_reverse_left": (-0.14, 0.12, 0.0),
    "diagonal_reverse_right": (-0.14, -0.12, 0.0),
    "curve_left": (0.16, 0.08, 0.25),
    "curve_right": (0.16, -0.08, -0.25),
    "stop": (0.0, 0.0, 0.0),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def elu(value):
    import numpy as np

    return np.where(value > 0.0, value, np.expm1(value))


def numpy_actor(observation, arrays: dict[str, object]):
    import numpy as np

    value = (observation - arrays["obs_mean"]) / np.sqrt(arrays["obs_var"] + 1.0e-5)
    value = np.clip(value, -5.0, 5.0)
    for index in range(3):
        value = elu(arrays[f"w{index}"] @ value + arrays[f"b{index}"])
    return np.clip(arrays["wout"] @ value + arrays["bout"], -1.0, 1.0)


def deployment_contract(
    *,
    forward_min: float,
    forward_max: float,
    lateral_min: float,
    lateral_max: float,
    yaw_max: float,
    planar_deadband: float,
    yaw_deadband: float,
    stance_action: list[float],
    action_limit_by_joint: list[float] | None = None,
    action_filter_alpha: float = 1.0,
    action_delta_limit: float = 0.34,
    position_target_scale_rad: float = 0.25,
    command_smoothing_time_s: float = 0.4,
    control_hz: int = 50,
) -> tuple[dict[str, list[float]], dict[str, object], dict[str, object]]:
    """Validate and build the hardware command and stationary-action contract."""

    if action_limit_by_joint is None:
        action_limit_by_joint = [1.0] * 12
    values = (
        forward_min, forward_max, lateral_min, lateral_max, yaw_max,
        planar_deadband, yaw_deadband, action_filter_alpha,
        action_delta_limit, position_target_scale_rad, command_smoothing_time_s,
        *stance_action, *action_limit_by_joint,
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Export contract values must be finite.")
    if not -0.18 <= forward_min <= 0.0:
        raise ValueError("Validated forward minimum must be within [-0.18, 0] m/s.")
    if not 0.0 < forward_max <= 0.22:
        raise ValueError("Validated forward maximum must be within (0, 0.22] m/s.")
    if not -0.16 <= lateral_min <= 0.0:
        raise ValueError("Validated lateral minimum must be within [-0.16, 0] m/s.")
    if not 0.0 <= lateral_max <= 0.16:
        raise ValueError("Validated lateral maximum must be within [0, 0.16] m/s.")
    if not 0.0 < yaw_max <= 0.25:
        raise ValueError("Validated yaw limit must be within (0, 0.25] rad/s.")
    if not 0.0 <= planar_deadband <= 0.25:
        raise ValueError("Stationary planar deadband must be within [0, 0.25] m/s.")
    if not 0.0 <= yaw_deadband <= 0.30:
        raise ValueError("Stationary yaw deadband must be within [0, 0.30] rad/s.")
    if len(stance_action) != 12 or any(not -1.0 <= value <= 1.0 for value in stance_action):
        raise ValueError(
            "Stationary stance action must contain 12 normalized values within [-1, 1]."
        )
    if len(action_limit_by_joint) != 12 or any(
        not 0.0 < value <= 1.0 for value in action_limit_by_joint
    ):
        raise ValueError(
            "Per-joint normalized action limits must contain 12 values within (0, 1]."
        )
    if not 0.0 < action_filter_alpha <= 1.0:
        raise ValueError("Action filter alpha must be within (0, 1].")
    if not 0.0 < action_delta_limit <= 1.0:
        raise ValueError("Normalized action slew limit must be within (0, 1].")
    if not 0.0 < position_target_scale_rad <= 0.5:
        raise ValueError("Position target scale must be within (0, 0.5] rad.")
    if isinstance(control_hz, bool) or not isinstance(control_hz, int):
        raise ValueError("Control rate must be a whole number of Hz.")
    if not 10 <= control_hz <= 100 or 1000 % control_hz:
        raise ValueError("Control rate must divide 1000 Hz and be within [10, 100] Hz.")
    frame_time_s = 1.0 / control_hz
    if not frame_time_s <= command_smoothing_time_s <= 2.0:
        raise ValueError(
            f"Command smoothing time must be within [{frame_time_s:.3f}, 2.0] s."
        )
    command_limits = {
        "forward_m_s": [forward_min, forward_max],
        "lateral_m_s": [lateral_min, lateral_max],
        "yaw_rate_rad_s": [-yaw_max, yaw_max],
    }
    stationary_contract = {
        "behavior": "slew_to_validated_four_foot_stance_action",
        "normalized_stance_action": stance_action,
        "planar_command_deadband_m_s": planar_deadband,
        "yaw_command_deadband_rad_s": yaw_deadband,
    }
    action_contract = {
        "actor_output_clip": [-1.0, 1.0],
        "applied_normalized_clip_by_joint": action_limit_by_joint,
        "low_pass_alpha": action_filter_alpha,
        "applied_normalized_slew_limit": action_delta_limit,
        "position_target_scale_rad": position_target_scale_rad,
    }
    return command_limits, stationary_contract, action_contract


def validate_goal_evaluation(evaluation: object) -> None:
    """Reject stale Goal results that predate the full mobility screen."""

    if not isinstance(evaluation, dict) or not evaluation.get("passed"):
        raise ValueError("Refusing to export a policy without a passing evaluation result.")
    if evaluation.get("stage") != "goal":
        raise ValueError("Real-robot export requires a passing Goal evaluation.")
    segments = evaluation.get("segments")
    if not isinstance(segments, dict):
        raise ValueError("Goal evaluation has no segment evidence.")
    missing = [name for name in REQUIRED_GOAL_COMMANDS if name not in segments]
    if missing:
        raise ValueError(
            "Goal evaluation predates the full mobility screen; missing: "
            + ", ".join(missing)
        )
    for name, expected in REQUIRED_GOAL_COMMANDS.items():
        segment = segments[name]
        if not isinstance(segment, dict):
            raise ValueError(f"Goal evaluation segment {name!r} is invalid.")
        try:
            actual = tuple(
                float(segment.get(key, math.nan))
                for key in ("command_forward", "command_lateral", "command_yaw")
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Goal evaluation segment {name!r} has invalid command evidence."
            ) from exc
        if any(abs(left - right) > 1.0e-6 for left, right in zip(actual, expected)):
            raise ValueError(
                f"Goal evaluation segment {name!r} used {actual}, expected {expected}."
            )


def validate_robust_test_evaluation(evaluation: object) -> None:
    """Allow a deliberately restricted hardware test without weakening promotion."""

    if not isinstance(evaluation, dict) or not evaluation.get("passed"):
        raise ValueError("Test-policy export still requires a passing evaluation result.")
    if evaluation.get("stage") != "robust":
        raise ValueError("Test-policy export requires a passing Robust evaluation.")
    segments = evaluation.get("segments")
    if not isinstance(segments, dict) or not segments:
        raise ValueError("Robust evaluation has no segment evidence.")


def validate_current_evaluation(
    evaluation: object, *, expected_stage: str = "current"
) -> None:
    """Require the complete mobility, posture, and current-validity screen."""
    from evaluate_simple_dog_policy import EXPECTED

    if not isinstance(evaluation, dict) or not evaluation.get("passed"):
        raise ValueError("CurrentV3 export requires a passing evaluation result.")
    if evaluation.get("stage") != expected_stage:
        raise ValueError(
            f"CurrentV3 export requires the {expected_stage} evaluation stage."
        )
    segments = evaluation.get("segments")
    if not isinstance(segments, dict):
        raise ValueError("CurrentV3 evaluation has no segment evidence.")
    missing = [name for name in EXPECTED["current"] if name not in segments]
    if missing:
        raise ValueError("CurrentV3 evaluation is incomplete; missing: " + ", ".join(missing))


def validate_current_body_evaluation(evaluation: object) -> None:
    """Require the locomotion-only mobility screen used by V17 and later."""
    from evaluate_simple_dog_policy import EXPECTED

    if not isinstance(evaluation, dict) or not evaluation.get("passed"):
        raise ValueError("CurrentBody export requires a passing evaluation result.")
    if evaluation.get("stage") != "currentbody":
        raise ValueError("CurrentBody export requires the currentbody evaluation stage.")
    segments = evaluation.get("segments")
    if not isinstance(segments, dict):
        raise ValueError("CurrentBody evaluation has no segment evidence.")
    missing = [name for name in EXPECTED["currentbody"] if name not in segments]
    if missing:
        raise ValueError(
            "CurrentBody evaluation is incomplete; missing: " + ", ".join(missing)
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--profile-id", required=True)
    parser.add_argument("--profile-sha", required=True)
    parser.add_argument("--evaluation", type=Path, required=True)
    parser.add_argument(
        "--allow-robust-test-policy",
        action="store_true",
        help=(
            "Export a passing Robust checkpoint as an explicitly unpromoted, "
            "restricted test policy. This does not relax the normal Goal gate."
        ),
    )
    parser.add_argument("--validated-forward-min", type=float, default=-0.18)
    parser.add_argument("--validated-forward-max", type=float, default=0.22)
    parser.add_argument("--validated-lateral-min", type=float, default=-0.16)
    parser.add_argument("--validated-lateral-max", type=float, default=0.16)
    parser.add_argument("--validated-yaw-max", type=float, default=0.25)
    parser.add_argument("--stationary-planar-deadband", type=float, default=0.02)
    parser.add_argument("--stationary-yaw-deadband", type=float, default=0.03)
    parser.add_argument(
        "--stationary-stance-action",
        type=float,
        nargs=12,
        required=True,
        help="Normalized 12-joint stance action validated for the robot profile.",
    )
    parser.add_argument(
        "--action-limit-by-joint",
        type=float,
        nargs=12,
        default=[1.0] * 12,
        help="Applied normalized action limits in policy-joint order.",
    )
    parser.add_argument(
        "--action-filter-alpha",
        type=float,
        default=1.0,
        help="First-order low-pass alpha applied to bounded actor actions.",
    )
    parser.add_argument(
        "--action-delta-limit",
        type=float,
        required=True,
        help="Maximum normalized change in the applied action per control frame.",
    )
    parser.add_argument(
        "--position-target-scale-rad",
        type=float,
        required=True,
        help="Radians of semantic joint residual represented by normalized action 1.",
    )
    parser.add_argument(
        "--control-hz",
        type=int,
        required=True,
        help=(
            "Policy and hardware control rate recorded in deployment metadata; "
            "pass the validated profile value explicitly."
        ),
    )
    parser.add_argument(
        "--command-smoothing-time-s",
        type=float,
        required=True,
        help="First-order command smoothing time used during training.",
    )
    parser.add_argument(
        "--current-fit",
        type=Path,
        help="Schema-2 physical simulation fit for a CurrentV3 checkpoint.",
    )
    parser.add_argument(
        "--current-body-426",
        action="store_true",
        help=(
            "Export the compact 24-frame/6-sample CurrentBody layout used by "
            "V14 and later locomotion policies. Requires --current-fit."
        ),
    )
    parser.add_argument("--flat-evaluation", type=Path)
    parser.add_argument("--stress-evaluation", type=Path)
    parser.add_argument(
        "--policy-semantics",
        nargs=12,
        help="Twelve control-profile joint semantics in policy order for CurrentV3.",
    )
    args = parser.parse_args()
    if args.current_body_426 and not args.current_fit:
        raise SystemExit("--current-body-426 requires --current-fit.")

    try:
        command_limits, stationary_contract, action_contract = deployment_contract(
            forward_min=args.validated_forward_min,
            forward_max=args.validated_forward_max,
            lateral_min=args.validated_lateral_min,
            lateral_max=args.validated_lateral_max,
            yaw_max=args.validated_yaw_max,
            planar_deadband=args.stationary_planar_deadband,
            yaw_deadband=args.stationary_yaw_deadband,
            stance_action=args.stationary_stance_action,
            action_limit_by_joint=args.action_limit_by_joint,
            action_filter_alpha=args.action_filter_alpha,
            action_delta_limit=args.action_delta_limit,
            position_target_scale_rad=args.position_target_scale_rad,
            command_smoothing_time_s=args.command_smoothing_time_s,
            control_hz=args.control_hz,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    evaluation = json.loads(args.evaluation.read_text(encoding="utf-8"))
    try:
        if args.current_body_426:
            validate_current_body_evaluation(evaluation)
        elif args.current_fit:
            validate_current_evaluation(evaluation)
        elif args.allow_robust_test_policy:
            validate_robust_test_evaluation(evaluation)
        else:
            validate_goal_evaluation(evaluation)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    if args.current_body_426:
        if args.flat_evaluation:
            raise SystemExit("CurrentBody export does not use a flat posture evaluation.")
        if not args.stress_evaluation:
            raise SystemExit("CurrentBody export requires --stress-evaluation from Push-Eval.")
        flat_evaluation = None
        stress_evaluation = json.loads(args.stress_evaluation.read_text(encoding="utf-8"))
        try:
            validate_current_body_evaluation(stress_evaluation)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    elif args.current_fit:
        if not args.flat_evaluation or not args.stress_evaluation:
            raise SystemExit(
                "CurrentV3 export requires --flat-evaluation and --stress-evaluation."
            )
        flat_evaluation = json.loads(args.flat_evaluation.read_text(encoding="utf-8"))
        stress_evaluation = json.loads(args.stress_evaluation.read_text(encoding="utf-8"))
        try:
            validate_current_evaluation(
                flat_evaluation, expected_stage="currentflat"
            )
            validate_current_evaluation(
                stress_evaluation, expected_stage="currentstress"
            )
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    else:
        if args.flat_evaluation or args.stress_evaluation:
            raise SystemExit("CurrentV3 evaluation-set arguments require --current-fit.")
        flat_evaluation = None
        stress_evaluation = None

    import numpy as np
    import torch

    checkpoint = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
    model = checkpoint["model"]
    arrays = {
        name: model[key].detach().cpu().float().numpy()
        for name, key in TENSOR_KEYS.items()
    }
    if args.current_fit:
        if not args.policy_semantics:
            raise SystemExit("CurrentV3 export requires --policy-semantics in profile order.")
        from current_policy_fit import load_current_policy_fit
        current_fit = load_current_policy_fit(args.current_fit, args.policy_semantics)
        if args.current_body_426:
            observation_size = 426
            hidden_sizes = (256, 256, 128)
        else:
            observation_size = 279
            hidden_sizes = (192, 192, 128)
    else:
        current_fit = None
        observation_size = 180
        hidden_sizes = (128, 128, 128)
    expected_shapes = {
        "obs_mean": (observation_size,), "obs_var": (observation_size,),
        "w0": (hidden_sizes[0], observation_size), "b0": (hidden_sizes[0],),
        "w1": (hidden_sizes[1], hidden_sizes[0]), "b1": (hidden_sizes[1],),
        "w2": (hidden_sizes[2], hidden_sizes[1]), "b2": (hidden_sizes[2],),
        "wout": (12, hidden_sizes[2]), "bout": (12,),
    }
    actual_shapes = {name: value.shape for name, value in arrays.items()}
    if actual_shapes != expected_shapes:
        raise SystemExit(f"Unexpected V2 actor shape: {actual_shapes}")

    generator = np.random.default_rng(42)
    for _ in range(8):
        observation = generator.normal(size=observation_size).astype(np.float32)
        torch_value = torch.from_numpy(observation)
        normalized = torch.clamp(
            (torch_value - model[TENSOR_KEYS["obs_mean"]].float())
            / torch.sqrt(model[TENSOR_KEYS["obs_var"]].float() + 1.0e-5),
            -5.0,
            5.0,
        )
        value = normalized
        for index in range(3):
            value = torch.nn.functional.elu(
                model[TENSOR_KEYS[f"w{index}"]].float() @ value
                + model[TENSOR_KEYS[f"b{index}"]].float()
            )
        expected = torch.clamp(
            model[TENSOR_KEYS["wout"]].float() @ value
            + model[TENSOR_KEYS["bout"]].float(),
            -1.0,
            1.0,
        ).numpy()
        actual = numpy_actor(observation, arrays)
        if not np.allclose(actual, expected, rtol=1.0e-5, atol=1.0e-6):
            raise SystemExit("Portable NumPy actor failed PyTorch parity validation.")

    args.output.mkdir(parents=True, exist_ok=True)
    weights_path = args.output / "policy_weights.npz"
    np.savez_compressed(weights_path, **arrays)
    metadata = {
        "schema_version": 4 if args.current_body_426 else (3 if current_fit else 2),
        "deployment_tier": (
            "current_body_426_locomotion_test" if args.current_body_426 else
            ("current_v3_promoted" if current_fit else
            ("restricted_robust_test" if args.allow_robust_test_policy else "goal_promoted")
            )
        ),
        "profile_id": args.profile_id,
        "profile_sha256": args.profile_sha,
        "checkpoint_sha256": sha256(args.checkpoint),
        "weights_sha256": sha256(weights_path),
        "checkpoint_epoch": int(checkpoint["epoch"]),
        "observation_size": observation_size,
        "observation_history": 24 if args.current_body_426 else 4,
        "observation_builder": (
            "current_body_v14_426" if args.current_body_426 else
            ("current_v3_279" if current_fit else "v2_180")
        ),
        "observation_frame": [
            "angular_velocity_body_rad_s[3]",
            "projected_gravity_body[3]",
            "body_command_forward_lateral_yaw[3]",
            "policy_joint_position_rad[12]",
            "0.05*policy_joint_velocity_rad_s[12]",
            "previous_applied_action[12]",
        ],
        "action_size": 12,
        "action_contract": action_contract,
        "control_hz": args.control_hz,
        "command_smoothing_time_s": args.command_smoothing_time_s,
        "stationary_action_contract": stationary_contract,
        "validated_command_limits": command_limits,
        "activation": "elu",
        "normalization_epsilon": 1.0e-5,
        "normalization_clip": [-5.0, 5.0],
        "evaluation": evaluation,
    }
    if current_fit:
        metadata["observation_frame"] += [
            "normalized_absolute_servo_current[12]",
            "servo_current_freshness_validity[12]",
        ]
        if args.current_body_426:
            metadata["observation_frame"].append("control_interval_over_20ms[1]")
            metadata["history_selection"] = {
                "frame_size": 70,
                "indices": [0, 5, 10, 15, 20, 23],
                "timing_reference_ms": 20.0,
            }
        metadata["posture_command"] = [
            "body_height_offset_m", "body_roll_rad", "body_pitch_rad",
        ]
        metadata["current_observation_contract"] = {
            "units": "mA",
            "absolute": True,
            "normalization_bias_ma": list(current_fit.current_bias_ma),
            "normalization_scale_ma": list(current_fit.current_scale_ma),
            "clip_normalized": [
                clip / scale
                for clip, scale in zip(current_fit.current_clip_ma, current_fit.current_scale_ma)
            ],
            "current_step_ma": 6.5,
            "missing_behavior": "hold_last_finite_and_validity_zero",
        }
        metadata["posture_command_contract"] = {
            "height_offset_m": [0.0, 0.0] if args.current_body_426 else [-0.035, 0.015],
            "roll_rad": [0.0, 0.0] if args.current_body_426 else [-0.12, 0.12],
            "pitch_rad": [0.0, 0.0] if args.current_body_426 else [-0.12, 0.12],
            "smoothing_time_s": 0.5,
            "layout": (
                "append_after_selected_history" if args.current_body_426
                else "append_after_history"
            ),
        }
        metadata["physical_fit"] = current_fit.metadata()
        if args.current_body_426:
            metadata["evaluation_set"] = {
                "held_out_rough_locomotion": evaluation,
                "randomized_push_stress": stress_evaluation,
            }
        else:
            metadata["evaluation_set"] = {
                "flat_regression": flat_evaluation,
                "held_out_rough": evaluation,
                "randomized_push_stress": stress_evaluation,
            }
    metadata_path = args.output / "policy_metadata.json"
    metadata_path.write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"weights": str(weights_path), "metadata": str(metadata_path)}))


if __name__ == "__main__":
    main()
