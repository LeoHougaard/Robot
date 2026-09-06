"""Fixed-command V21 acquisition comparison. This cannot promote a delivery policy."""
import argparse
import faulthandler
import hashlib
import json
import os
from pathlib import Path
import traceback
import importlib

faulthandler.enable()
startup_timeout = int(os.environ.get("SIMPLE_DOG_STARTUP_TIMEOUT_S", "0"))
if startup_timeout != 0 and not 30 <= startup_timeout <= 600:
    raise ValueError("startup timeout must be zero or 30..600 seconds")
faulthandler.dump_traceback_later(startup_timeout or 120,
                               repeat=not bool(startup_timeout), exit=bool(startup_timeout))
print("STRIDE_EVALUATOR_IMPORT", flush=True)
from isaaclab.app import AppLauncher

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--checkpoint", type=Path, required=True)
parser.add_argument("--family", choices=("v21", "v22"), default="v21")
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--num-envs", type=int, default=8)
parser.add_argument("--seed", type=int, default=42)
parser.add_argument("--seconds", type=int, default=20,
                    help="20-second matched comparison or longer flat endurance check (up to 120 s)")
parser.add_argument("--commands", action="store_true", help="Screen slow isolated axes after four seconds forward")
parser.add_argument("--stage", choices=("commands", "speed", "rough"), default="commands",
                    help="V22 command configuration or fixed Rough125 exploratory screen")
parser.add_argument("--terrain-kind", choices=("mixture", "uniform", "up", "down"), default="mixture",
                     help="Rough screen terrain kind; mixture is the fixed training distribution")
parser.add_argument("--terrain-height-fraction", type=float, choices=(.125, .25, .5, .75, 1.0), default=.125,
                     help="Reviewed fixed terrain height fraction for rough screens")
parser.add_argument("--variation", choices=("nominal", "v20-train-envelope"), default="nominal",
                    help="Enable the documented V20 physical/sensor randomization envelope")
parser.add_argument("--timing-assessment", action="store_true",
                    help="Use the existing 18..39 ms V4 timing/sensor-age assessment bounds")
parser.add_argument("--start-stationary", action="store_true",
                    help="Command-screen variant: stand for the first four seconds before requesting motion")
parser.add_argument("--command-index", type=int, help="Select one slow command for a single-robot video")
parser.add_argument(
    "--fidelity-asset",
    type=Path,
    help="evidence-only asset swap; training profile and checkpoint contract remain authoritative",
)
parser.add_argument("--video-folder", type=Path)
AppLauncher.add_app_launcher_args(parser)
args = parser.parse_args()
if args.stage in ("speed", "rough") and (args.family != "v22" or not args.commands):
    parser.error("speed and rough stages require V22 and --commands")
if not 20 <= args.seconds <= 120:
    parser.error("seconds must be between 20 and 120")
if args.command_index is not None and (not args.commands or not 0 <= args.command_index < (14 if args.stage in ("speed", "rough") else 8)):
    parser.error(f"command-index requires --commands and an index for the selected {args.stage} menu")
if args.start_stationary and not args.commands:
    parser.error("start-stationary requires --commands")
if args.stage == "rough" and args.variation != "v20-train-envelope":
    parser.error("rough screens require the documented V20 training envelope")
if args.stage == "rough" and args.terrain_kind == "mixture":
    parser.error("rough screen requires an explicit held-out terrain kind")
if args.variation != "nominal" and not args.commands:
    parser.error("variation requires --commands")
if args.variation != "nominal" and args.family != "v22":
    parser.error("variation is registered only for V22")
if args.timing_assessment and (args.variation == "nominal" or args.family != "v22"):
    parser.error("timing-assessment requires V22 variation")
if args.output.exists() or (args.video_folder and args.video_folder.exists()):
    parser.error("refusing to overwrite evidence")
if f"quadruped_current_body_{args.family}_" not in str(args.checkpoint):
    parser.error("checkpoint must belong to the explicitly selected policy family")
if args.fidelity_asset is not None and args.family != "v22":
    parser.error("--fidelity-asset is supported only for V22 contract-bound evidence")
if args.video_folder:
    args.enable_cameras = True


def fidelity_provenance(profile):
    if args.fidelity_asset is None:
        return None
    active_asset = Path(profile["robot"]["asset_usd"]).resolve()
    selected_asset = args.fidelity_asset.resolve()
    manifest_path = active_asset.with_suffix(".manifest.json")
    if not manifest_path.is_file():
        raise ValueError("fidelity requires the active asset conversion manifest")
    manifest = json.loads(manifest_path.read_text())
    manifest_output = Path(manifest.get("output", "")).resolve()
    manifest_source = Path(manifest.get("source", "")).resolve()
    if active_asset != manifest_output or selected_asset != manifest_source:
        raise ValueError("fidelity asset pair does not match the conversion manifest")
    if manifest.get("unchanged_attribute_check") is not True:
        raise ValueError("conversion manifest did not preserve unchanged attributes")
    layer_hashes = manifest.get("source_layers", {})
    if not layer_hashes or any(
        hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest
        for path, digest in layer_hashes.items()
    ):
        raise ValueError("conversion manifest source layer hashes no longer match")
    output_sha256 = hashlib.sha256(active_asset.read_bytes()).hexdigest()
    if output_sha256 != manifest.get("output_sha256"):
        raise ValueError("conversion manifest output hash no longer matches")
    alternate = json.loads(json.dumps(profile))
    alternate["robot"]["asset_usd"] = str(selected_asset)
    differences = []

    def compare(left, right, path=""):
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                compare(left.get(key), right.get(key), path + "." + key)
        elif left != right:
            differences.append(path)

    compare(profile, alternate)
    if differences != [".robot.asset_usd"]:
        raise ValueError("fidelity profile must differ from training profile only by asset_usd")
    from robot_control_profile import canonical_hash
    return dict(
        training_profile_sha256=canonical_hash(profile),
        fidelity_profile_sha256=canonical_hash(alternate),
        profile_differences=differences,
        active_asset=str(active_asset),
        active_asset_sha256=output_sha256,
        fidelity_asset=str(selected_asset),
        fidelity_asset_sha256=hashlib.sha256(selected_asset.read_bytes()).hexdigest(),
        conversion_manifest=str(manifest_path),
        conversion_manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        conversion_source_sha256=layer_hashes[str(manifest_source)],
        conversion_output_sha256=manifest.get("output_sha256"),
        unchanged_attribute_check=True,
    )
print("STRIDE_SIM_START", flush=True)
app = AppLauncher(args).app
faulthandler.cancel_dump_traceback_later()
print("STRIDE_SIM_READY", flush=True)

import gymnasium as gym
import torch
import numpy as np
from isaaclab_tasks.utils import parse_env_cfg
from initialize_delivery_stride import build_actor
importlib.import_module("simple_dog_task_current_body_" + args.family)


def evaluate():
    training_profile = None
    fidelity = None
    if args.fidelity_asset is not None:
        from robot_control_profile import load_control_profile
        training_profile = load_control_profile()
        fidelity = fidelity_provenance(training_profile)
    from verify_delivery_terrain import verify
    terrain_verification = verify()
    stage = "Rough125" if args.stage == "rough" else ("Variation" if args.variation != "nominal" else ("Speed" if args.commands and args.stage == "speed" else ("Commands" if args.commands else "Acquire")))
    task = f"Isaac-Locomotion-CurrentBody{args.family.upper()}-{stage}-Simple-Dog-Direct-v0"
    cfg = parse_env_cfg(task, device=args.device, num_envs=args.num_envs)
    if args.stage == "rough":
        from delivery_terrain import terrain_for_height
        cfg.terrain = terrain_for_height(args.terrain_height_fraction, args.terrain_kind, tile_size=8.0)
        cfg.terrain_height_fraction = args.terrain_height_fraction
        cfg.terrain_kind = args.terrain_kind
        from simple_dog_task_current_body_v22.env_cfg import CadStrideSpeedCfg
        cfg.stride_command_menu = CadStrideSpeedCfg().stride_command_menu
    if args.stage == "speed" and args.variation != "nominal":
        from simple_dog_task_current_body_v22.env_cfg import CadStrideSpeedCfg
        cfg.stride_command_menu = CadStrideSpeedCfg().stride_command_menu
    if fidelity is not None:
        cfg.robot.spawn.usd_path = fidelity["fidelity_asset"]
    cfg.seed = args.seed
    if args.timing_assessment:
        # These are the existing V4 training bounds, selected from the
        # Aug-29 feedback timing (median 23 ms, p95 31 ms, max 39 ms).
        cfg.timing_interval_ms = (18., 39.)
        cfg.action_delay_steps = (0, 1)
    cfg.episode_length_s = args.seconds + 10.
    if args.commands:
        cfg.stride_evaluate_commands = True
        if args.start_stationary:
            cfg.stride_command = (0., 0., 0.)
        if args.command_index is not None:
            cfg.stride_command_menu = (cfg.stride_command_menu[args.command_index],)
        elif args.num_envs % len(cfg.stride_command_menu):
            raise ValueError(f"{args.stage} command screen requires complete menu groups or --command-index")
    if args.stage == "rough":
        assert cfg.terrain.terrain_type == "generator" and cfg.terrain.terrain_generator is not None
        assert cfg.terrain.terrain_generator.size == (8.0, 8.0)
    else:
        assert cfg.terrain.terrain_type == "plane" and cfg.terrain.terrain_generator is None
    rough_provenance = None
    if args.stage == "rough":
        generator = cfg.terrain.terrain_generator
        rough_provenance = dict(seed=generator.seed, tile_size_m=list(generator.size),
                                horizontal_scale_m=generator.horizontal_scale,
                                vertical_scale_m=generator.vertical_scale,
                                difficulty_range=list(generator.difficulty_range),
                                terrain_kind=args.terrain_kind,
                                terrain_height_fraction=args.terrain_height_fraction,
                                terrain_relative_height_metric="_body_posture height minus terrain ray hits",
                                sub_terrains=sorted(generator.sub_terrains))
        mesh_stats = {}
        for name, sub_cfg in generator.sub_terrains.items():
            if name == "floor":
                mesh_stats[name] = dict(z_min_m=0., z_max_m=0., spawn_origin_m=[0., 0., 0.])
                continue
            sub_cfg.size = generator.size
            sub_cfg.horizontal_scale = generator.horizontal_scale
            sub_cfg.vertical_scale = generator.vertical_scale
            np.random.seed(generator.seed)
            meshes, origin = sub_cfg.function(1., sub_cfg)
            z = np.concatenate([mesh.vertices[:, 2] for mesh in meshes])
            mesh_stats[name] = dict(z_min_m=float(z.min()), z_max_m=float(z.max()),
                                    spawn_origin_m=np.asarray(origin, dtype=float).tolist())
        # These independently generated meshes check the configured shape.
        # Record actual imported terrain and sampled ray heights separately.
        rough_provenance["reference_generated_mesh_stats"] = mesh_stats
    env = gym.make(task, cfg=cfg, render_mode="rgb_array" if args.video_folder else None)
    if args.video_folder:
        env = gym.wrappers.RecordVideo(env, video_folder=str(args.video_folder),
                                      step_trigger=lambda s: s == 0, video_length=args.seconds * 50, disable_logger=True)
    base = env.unwrapped
    variation_initial = base.variation_snapshot() if args.variation != "nominal" else None
    try:
        params, actor = build_actor(Path(__file__).parent / f"simple_dog_task_current_body_{args.family}/agents/rl_games_ppo_cfg.yaml")
        if args.family == "v22":
            from robot_control_profile import load_control_profile
            from verify_delivery_coordinates import verify
            root = Path(__file__).parent
            training_profile = training_profile or load_control_profile()
            verify(training_profile, root / "fits" / cfg.stride_reference_filename,
                   root / "fits/servo-response-20260829.json", params["config"]["delivery_policy_contract"])
        state = torch.load(args.checkpoint, map_location="cpu", weights_only=False)
        if params["config"].get("delivery_policy_contract") != state.get("delivery_policy_contract"):
            raise ValueError("checkpoint coordinate/reference contract does not match the evaluation")
        actor.load_state_dict(state["model"], strict=True)
        actor.to(base.device).eval()
        obs, _ = env.reset()
        if args.stage == "rough":
            rough_provenance["initial_spawn_origins_m"] = base.scene.env_origins.detach().cpu().tolist()
            initial_height, initial_roll, initial_pitch = base._body_posture()
            rough_provenance["initial_terrain_relative_height_m"] = initial_height[:min(8, args.num_envs)].detach().cpu().tolist()
            rough_provenance["initial_rays_finite"] = True
            hits = base._height_scanner.data.ray_hits_w.torch[..., 2]
            rough_provenance["initial_ray_z_range_m"] = [float(hits.min()), float(hits.max())]
            rough_provenance["observed_ray_z_range_m"] = [float(hits.min()), float(hits.max())]
        if args.variation != "nominal":
            # V4 ramps current randomization during training.  Evaluation
            # starts at step zero, so sample the configured envelope explicitly
            # after reset and record the realized values.
            base._current_effort_scale.uniform_(*cfg.current_effort_scale_randomization)
            base._current_dropout_probability.uniform_(0., cfg.current_dropout_probability_max)
        variation_after_reset = base.variation_snapshot() if args.variation != "nominal" else None
        sums = torch.zeros(args.num_envs, 6, device=base.device)
        resets = torch.zeros(args.num_envs, device=base.device)
        air = torch.zeros(args.num_envs, 4, device=base.device)
        landings = torch.zeros_like(air)
        previous_contact = torch.ones_like(air, dtype=torch.bool)
        air_streak = torch.zeros_like(air)
        max_air_streak = torch.zeros_like(air)
        yaw_sum = torch.zeros(args.num_envs, device=base.device)
        command_sums = torch.zeros(args.num_envs, 5, device=base.device)
        max_tilt = torch.zeros_like(yaw_sum)
        min_height = torch.full_like(yaw_sum, float("inf"))
        windows = []
        window_sum = torch.zeros(args.num_envs, 6, device=base.device)
        window_steps = 0
        total_steps = args.seconds * 50
        measured_steps = total_steps - 300
        expected_command = base._commands.clone()
        fixed_command = torch.tensor([cfg.stride_command_menu[i % len(cfg.stride_command_menu)]
                                      for i in range(args.num_envs)], device=base.device) if args.commands else None
        with torch.inference_mode():
            for step in range(total_steps):
                assert obs["policy"].shape == (args.num_envs, 428)
                assert obs["critic"].shape == (args.num_envs, 438)
                assert torch.equal(obs["policy"][:, 420:423], base._commands)
                if not torch.isfinite(obs["policy"]).all():
                    raise RuntimeError("nonfinite actor observation")
                residual = actor(dict(is_train=False, prev_actions=None,
                                      obs=obs["policy"], rnn_states=None))["mus"]
                if not torch.isfinite(residual).all():
                    raise RuntimeError("nonfinite residual")
                obs, reward, terminated, truncated, _ = env.step(residual.clamp(-1., 1.))
                resets += terminated | truncated
                if args.commands and not resets.any():
                    if base._posture_commands.any() or base._posture_targets.any():
                        raise RuntimeError("motion-only stage emitted a posture request")
                    if step >= round(cfg.stride_initial_forward_s / base.step_dt) - 1:
                        alpha = min(1., base.step_dt / cfg.command_smoothing_time_s)
                        expected_command += alpha * (fixed_command - expected_command)
                    if not torch.allclose(base._commands, expected_command, atol=1e-7, rtol=0):
                        raise RuntimeError(f"command timing/smoothing mismatch at step {step}")
                contact = base._contact_sensor.data.current_contact_time.torch[:, base._feet_sensor_ids] > 0
                if step >= 300:
                    motion = base._semantic_vector_b(base._robot.data.root_lin_vel_b.torch)
                    height, roll, pitch = base._body_posture()
                    if rough_provenance is not None:
                        hits = base._height_scanner.data.ray_hits_w.torch[..., 2]
                        bounds = rough_provenance["observed_ray_z_range_m"]
                        bounds[0] = min(bounds[0], float(hits.min()))
                        bounds[1] = max(bounds[1], float(hits.max()))
                    tilt = torch.sqrt(roll.square() + pitch.square())
                    values = torch.stack((motion[:, 0], motion[:, 1].abs(), tilt, height, reward,
                                          (residual.abs() >= 1.).float().mean(-1)), dim=-1)
                    sums += values
                    window_sum += values
                    window_steps += 1
                    yaw_rate = base._semantic_vector_b(base._robot.data.root_ang_vel_b.torch)[:, 2]
                    yaw_sum += yaw_rate.abs()
                    command_sums += torch.stack((motion[:, 1], yaw_rate, motion[:, 0].abs(),
                        torch.linalg.vector_norm(motion[:, :2] - base._commands[:, :2], dim=-1),
                        (yaw_rate-base._commands[:, 2]).abs()), dim=-1)
                    max_tilt = torch.maximum(max_tilt, tilt)
                    min_height = torch.minimum(min_height, height)
                    air_streak = torch.where(contact, 0., air_streak + 1.)
                    max_air_streak = torch.maximum(max_air_streak, air_streak)
                    air += ~contact
                    landings += contact & ~previous_contact
                    if window_steps == 250 or step == total_steps - 1:
                        windows.append(dict(end_seconds=(step + 1) / 50,
                                            measured_seconds=window_steps / 50,
                                            mean_values=(window_sum / window_steps).cpu().tolist()))
                        window_sum.zero_()
                        window_steps = 0
                previous_contact = contact
        rows = []
        for i, values in enumerate((sums / measured_steps).cpu().tolist()):
            row = dict(zip(("mean_forward_m_s", "mean_abs_lateral_m_s", "mean_tilt_rad",
                            "mean_height_m", "mean_reward", "residual_clip_fraction"), values))
            row.update(resets=int(resets[i]), landings_frflbrbl=landings[i].cpu().tolist(),
                       air_fraction_frflbrbl=(air[i] / measured_steps).cpu().tolist(),
                       max_continuous_air_s_frflbrbl=(max_air_streak[i] / 50).cpu().tolist(),
                       mean_abs_yaw_rate_rad_s=float(yaw_sum[i] / measured_steps),
                       max_tilt_rad=float(max_tilt[i]), min_height_m=float(min_height[i]))
            row.update(zip(("mean_signed_lateral_m_s", "mean_signed_yaw_rate_rad_s", "mean_abs_forward_m_s",
                            "mean_planar_error_m_s", "mean_abs_yaw_error_rad_s"),
                           (command_sums[i] / measured_steps).cpu().tolist()))
            row["command"] = list(cfg.stride_command_menu[i % len(cfg.stride_command_menu)]
                                  if args.commands else cfg.stride_command)
            rows.append(row)
        return dict(completed=True, policy_family="current_body_" + args.family,
                    joint_coordinate_convention=cfg.joint_coordinate_convention,
                    checkpoint_sha256=hashlib.sha256(args.checkpoint.read_bytes()).hexdigest(),
                    source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                    seed=args.seed, control_hz=50, simulation_seconds=args.seconds,
                    measured_seconds=measured_steps / 50,
                    window_columns=["mean_forward_m_s", "mean_abs_lateral_m_s", "mean_tilt_rad",
                                    "mean_height_m", "mean_reward", "residual_clip_fraction"],
                    windows=windows,
                    terrain=(args.terrain_kind if args.stage == "rough" else "plane"), rough_terrain_provenance=rough_provenance, terrain_height_fraction=(args.terrain_height_fraction if args.stage == "rough" else 0.), terrain_tile_size_m=(8.0 if args.stage == "rough" else None), terrain_verification=terrain_verification, stage=stage,
                    command_stage=args.stage,
                    fidelity_provenance=fidelity if args.family == "v22" else None,
                    initial_command=list(cfg.stride_command), start_stationary=args.start_stationary,
                    command=None if args.commands else [.04, 0., 0.], command_menu=(
                        [list(command) for command in cfg.stride_command_menu] if args.commands else None), results=rows,
                    variation=("v20-train-envelope" if args.variation != "nominal" else "nominal"),
                    timing_assessment=bool(args.timing_assessment),
                    variation_config=(dict(domain_randomization_enabled=cfg.domain_randomization_enabled,
                                           observation_noise_enabled=cfg.observation_noise_enabled,
                                           action_delay_steps=list(cfg.action_delay_steps),
                                           timing_interval_ms=list(cfg.timing_interval_ms),
                                           base_mass_scale=list(cfg.base_mass_scale),
                                           link_mass_scale=list(cfg.link_mass_scale),
                                           independent_inertia_scale=list(cfg.independent_inertia_scale),
                                           actuator_drive_scale=list(cfg.actuator_drive_scale),
                                           actuator_effort_scale=list(cfg.actuator_effort_scale),
                                           actuator_velocity_scale=list(cfg.actuator_velocity_scale),
                                           robot_static_friction_range=list(cfg.robot_static_friction_range),
                                           robot_dynamic_friction_range=list(cfg.robot_dynamic_friction_range),
                                           robot_restitution_range=list(cfg.robot_restitution_range),
                                           current_dropout_probability_max=cfg.current_dropout_probability_max,
                                           current_effort_scale_randomization=list(cfg.current_effort_scale_randomization),
                                           gyro_noise=cfg.gyro_noise, joint_position_noise=cfg.joint_position_noise,
                                           accelerometer_noise_mg=cfg.accelerometer_noise_mg)),
                    realized_samples=(dict(current_effort_scale_min=float(base._current_effort_scale.min().item()),
                                           current_effort_scale_max=float(base._current_effort_scale.max().item()),
                                           current_dropout_probability_min=float(base._current_dropout_probability.min().item()),
                                           current_dropout_probability_max=float(base._current_dropout_probability.max().item()),
                                           actuator_delay_steps_min=int(base._servo_trajectory.delay.min().item()),
                                           actuator_delay_steps_max=int(base._servo_trajectory.delay.max().item()))
                                      if args.variation != "nominal" else None),
                    realized_snapshots=(dict(initial=variation_initial, after_reset=variation_after_reset)
                                        if args.variation != "nominal" else None),
                    limitation=("exploratory rough terrain screen with Sustained physical/sensor variation and 18..39 ms sensor age; physics remains 20 ms; no deployment acceptance"
                                if args.stage == "rough" else (("timing assessment uses existing V4 bounds (18..39 ms) and one-frame action delay; timing input is sensor age only, physics remains 20 ms"
                                 if args.timing_assessment else "slow isolated command screen; flat physical/sensor variation only; no timing interval variation because V20Train documents (20,20), no deployment acceptance")
                                if args.variation != "nominal" else ("slow isolated command screen only; no mixed commands, terrain, model variation or deployment acceptance"
                                if args.commands else "acquisition comparison only; no turning, stopping, terrain or deployment acceptance"))))
    finally:
        env.close()


try:
    result = evaluate()
except Exception:
    result = dict(completed=False, error=traceback.format_exc())
finally:
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    app.close()
if not result["completed"]:
    raise SystemExit(1)
