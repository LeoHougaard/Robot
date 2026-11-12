"""CPU-only steady-state reference feasibility probe; no physics or motion.

Reports unclipped targets and residual correction room before the existing
clamp/filter/slew/servo pipeline. This is diagnostic evidence, not an experiment
acceptance gate, foot-clearance measurement, or permission to change limits.
"""
import ast
import json
from pathlib import Path

import torch

from delivery_gait import combine_stride

ROOT = Path(__file__).parent


def load_probe_inputs():
    """Read the real 14-entry speed menu without importing an Isaac config."""
    tree = ast.parse((ROOT / "simple_dog_task_current_body_v22/env_cfg.py").read_text())
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
               and node.name == "CadStrideSpeedCfg")
    menu = next(ast.literal_eval(node.value) for node in cls.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "stride_command_menu"
                        for target in node.targets))
    profile = json.loads((ROOT.parent / "control_center/profiles/assembly-four-leg-linkage-12dof-delivery.json").read_text())
    return menu, profile["environment"]


def action_headroom(raw, limits, residual_scale):
    """Feasible residual-action interval per sample/joint, before clamping.

    residual_scale is the largest correction in normalized action units, not
    radians. An empty [lower, upper] interval means even the best bounded
    residual cannot bring this reference component inside the existing limit.
    Signed margin is negative when the zero-residual reference already clips.
    """
    limits = torch.as_tensor(limits, dtype=raw.dtype, device=raw.device)
    if not torch.isfinite(raw).all() or not torch.isfinite(limits).all():
        raise ValueError("nonfinite reference or action limits")
    if not bool((limits > 0.).all()) or not 0. <= residual_scale < float("inf"):
        raise ValueError("limits must be positive and residual scale finite and nonnegative")
    # The ordinary pre-physics path first applies a global [-1, 1] clamp.
    limits = limits.clamp(max=1.)
    margin = limits - raw.abs()
    lower = (-limits - raw).clamp_min(-residual_scale)
    upper = (limits - raw).clamp_max(residual_scale)
    return dict(signed_margin=margin, residual_lower=lower, residual_upper=upper,
                feasible=lower <= upper,
                positive_headroom=(limits - raw).clamp(min=0., max=residual_scale),
                negative_headroom=(limits + raw).clamp(min=0., max=residual_scale),
                bilateral_headroom=margin.clamp(min=0., max=residual_scale),
                full_residual_envelope_fits=margin >= residual_scale)


def probe_reference(reference, commands, action_limits, *, phases=201, dt=.02):
    if phases < 2 or dt <= 0.:
        raise ValueError("probe needs at least two samples and a positive interval")
    command = torch.tensor(commands, dtype=torch.float32).repeat_interleave(phases, dim=0)
    n = len(command)
    if n == 0:
        raise ValueError("probe needs at least one command")
    posture = torch.zeros(n, 3)
    gravity = torch.tensor([[0., 0., -1.]]).repeat(n, 1)
    # Cover more than two full cycles at full ramp for the present 0.6 Hz fit.
    elapsed = torch.tile(reference["settle_seconds"] + reference["ramp_seconds"]
                         + torch.arange(phases) * dt, (len(commands),))
    raw = combine_stride(torch.zeros(n, 12), command, posture, gravity, elapsed, reference)
    limits = torch.as_tensor(action_limits, dtype=raw.dtype).clamp(max=1.)
    headroom = action_headroom(raw, limits, reference["residual_scale"])
    clipped = raw.clamp(-limits, limits)
    scale = reference["position_target_scale_rad"]
    raw_target, clipped_target = raw * scale, clipped * scale
    # Rates are finite differences of the reference only. No claim is made
    # about output rates after filtering, slew limits or the servo dynamics.
    raw_rates = raw_target.reshape(len(commands), phases, 12).diff(dim=1).abs() / dt
    clipped_rates = clipped_target.reshape(len(commands), phases, 12).diff(dim=1).abs() / dt

    def summarize(start, end):
        values, applied = raw[start:end], clipped[start:end]
        margin = headroom["signed_margin"][start:end]
        feasible = headroom["feasible"][start:end]
        return dict(raw_peak_action=float(values.abs().max()),
                    raw_peak_joint_target_rad=float(values.abs().max() * scale),
                    clipped_peak_joint_target_rad=float(applied.abs().max() * scale),
                    clipped_joint_fraction=float((margin < 0.).float().mean()),
                    any_joint_clipped_phase_fraction=float((margin < 0.).any(dim=-1).float().mean()),
                    min_signed_limit_margin_action=float(margin.min()),
                    min_bilateral_residual_headroom_action=float(headroom["bilateral_headroom"][start:end].min()),
                    min_bilateral_residual_headroom_rad=float(headroom["bilateral_headroom"][start:end].min() * scale),
                    full_residual_envelope_fits_fraction=float(headroom["full_residual_envelope_fits"][start:end].float().mean()),
                    residual_cannot_recover_joint_fraction=float((~feasible).float().mean()),
                    residual_cannot_recover_phase_fraction=float((~feasible).any(dim=-1).float().mean()),
                    raw_peak_action_by_joint=values.abs().amax(dim=0).tolist(),
                    min_signed_limit_margin_action_by_joint=margin.amin(dim=0).tolist(),
                    min_positive_residual_headroom_action_by_joint=headroom["positive_headroom"][start:end].amin(dim=0).tolist(),
                    min_negative_residual_headroom_action_by_joint=headroom["negative_headroom"][start:end].amin(dim=0).tolist(),
                    residual_cannot_recover_fraction_by_joint=(~feasible).float().mean(dim=0).tolist())

    report = summarize(0, n)
    report.update(command_count=len(commands), phase_samples_per_command=phases,
                  sample_interval_s=dt, ramp_complete=True,
                  residual_scale_action=reference["residual_scale"],
                  residual_scale_rad=reference["residual_scale"] * scale,
                  action_limits=limits.tolist(),
                  raw_peak_joint_rate_rad_s=float(raw_rates.max()),
                  clipped_peak_joint_rate_rad_s=float(clipped_rates.max()),
                  commands=[dict(index=i, command=list(command), **summarize(i * phases, (i + 1) * phases))
                            for i, command in enumerate(commands)],
                  limitation="local linear steady-state reference only; no filtering/slew/servo/contact simulation, physical foot clearance, or acceptance gate")
    return raw, clipped_target, report


def main():
    commands, environment = load_probe_inputs()
    reports = {}
    for name in ("standard", "highlift"):
        filename = "stride-reference-cad" + ("-highlift" if name == "highlift" else "") + "-20260906.json"
        reference = json.loads((ROOT / "fits" / filename).read_text())
        if reference["position_target_scale_rad"] != environment["action_scale"]:
            raise ValueError("reference and delivery profile action scales differ")
        reports[name] = probe_reference(reference, commands, environment["action_limit_by_joint"])[2]
    print(json.dumps(reports, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
