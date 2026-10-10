"""CPU contract and raw pre-clamp feasibility checks for the high-lift fit."""
import json
from pathlib import Path

import pytest
import torch

from probe_delivery_gait import action_headroom, load_probe_inputs, probe_reference

ROOT = Path(__file__).parent


def _reference(highlift=True):
    name = "stride-reference-cad" + ("-highlift" if highlift else "") + "-20260906.json"
    return json.loads((ROOT / "fits" / name).read_text())


def _run(reference, commands):
    _, environment = load_probe_inputs()
    assert reference["position_target_scale_rad"] == environment["action_scale"]
    return probe_reference(reference, commands, environment["action_limit_by_joint"])


def test_contract_and_zero_stop():
    old, new = _reference(False), _reference()
    assert old["kind"] == "stride_reference_cad_v1"
    assert new["kind"] == "stride_reference_cad_v2"
    assert new["lift_m"] == .03
    assert new["lift_full_planar_speed_m_s"] == .02
    assert new["lift_full_yaw_rate_rad_s"] == .1
    raw, target, report = _run(new, [[0., 0., 0.]])
    assert torch.equal(raw, torch.zeros_like(raw))
    assert torch.equal(target, torch.zeros_like(target))
    assert report["clipped_joint_fraction"] == 0.
    assert report["full_residual_envelope_fits_fraction"] == 1.


def test_dense_actual_14_command_probe_exposes_raw_targets_and_limited_headroom():
    commands, _ = load_probe_inputs()
    assert len(commands) == 14
    old_raw, _, old = _run(_reference(False), commands)
    new_raw, new_target, new = _run(_reference(), commands)
    for raw, report in ((old_raw, old), (new_raw, new)):
        assert torch.isfinite(raw).all()
        assert report["command_count"] == len(report["commands"]) == 14
        assert report["raw_peak_joint_target_rad"] == pytest.approx(float(raw.abs().max()) * .3)
        assert report["raw_peak_joint_rate_rad_s"] > 0.
        assert report["clipped_peak_joint_rate_rad_s"] > 0.
        assert 0. <= report["residual_cannot_recover_joint_fraction"] <= report["clipped_joint_fraction"] <= 1.
    assert new["raw_peak_joint_target_rad"] > old["raw_peak_joint_target_rad"]
    # The actual menu fits at zero residual, but lacks the full +/-0.15
    # correction margin. This diagnoses the fit; it is not an acceptance gate.
    assert new["raw_peak_joint_target_rad"] == new["clipped_peak_joint_target_rad"]
    assert new["clipped_peak_joint_target_rad"] == pytest.approx(float(new_target.abs().max()))
    assert new["clipped_joint_fraction"] == 0.
    assert 0. < new["min_signed_limit_margin_action"] < new["residual_scale_action"]
    assert 0. < new["min_bilateral_residual_headroom_action"] < new["residual_scale_action"]
    assert new["full_residual_envelope_fits_fraction"] < 1.
    print(json.dumps({name: {key: value for key, value in report.items() if key != "commands"}
                      for name, report in (("standard", old), ("highlift", new))}, allow_nan=False))


def test_headroom_intervals_account_for_cancellation_and_unrecoverable_reference():
    raw = torch.tensor([[0., .95, 1.10, 1.20, -1.10]])
    result = action_headroom(raw, [1.] * 5, .15)
    torch.testing.assert_close(result["signed_margin"], torch.tensor([[1., .05, -.10, -.20, -.10]]))
    torch.testing.assert_close(result["bilateral_headroom"], torch.tensor([[.15, .05, 0., 0., 0.]]))
    assert result["feasible"].tolist() == [[True, True, True, False, True]]
    # 1.10 needs at least -0.10 correction; -1.10 needs at least +0.10.
    assert float(result["residual_upper"][0, 2]) == pytest.approx(-.10)
    assert float(result["residual_lower"][0, 4]) == pytest.approx(.10)
    assert result["full_residual_envelope_fits"].tolist() == [[True, False, False, False, False]]


def test_full_residual_envelope_flag_agrees_with_endpoint_clamping():
    commands, environment = load_probe_inputs()
    raw, _, _ = _run(_reference(), commands)
    limits = torch.tensor(environment["action_limit_by_joint"])
    scale = _reference()["residual_scale"]
    result = action_headroom(raw, limits, scale)
    both_endpoints_fit = ((raw - scale >= -limits) & (raw + scale <= limits))
    assert torch.equal(result["full_residual_envelope_fits"], both_endpoints_fit)
    closest = raw + (-raw).clamp(-scale, scale)
    assert torch.equal(result["feasible"], closest.abs() <= limits)


def test_v2_full_lift_at_slow_lateral_and_turn():
    spec = _reference()
    spec["nominal_feet_m"] = [[0., 0., 0.]] * 4
    spec["inverse_jacobians"] = [[[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]] * 4
    for command in ([0., .02, 0.], [0., 0., .10]):
        raw, _, _ = _run(spec, [command])
        assert abs(float(raw[:, 2::3].abs().max()) - .1) < 1e-5


def test_synthetic_infeasible_probe_reports_clipping_before_it_is_hidden():
    # Deliberately impossible fixture tests diagnostic sensitivity; no saved
    # reference, training menu, action limit, or experiment gate is changed.
    spec = _reference()
    spec["lift_m"] *= 3.
    raw, target, report = _run(spec, [[.04, 0., 0.]])
    assert report["raw_peak_joint_target_rad"] > report["clipped_peak_joint_target_rad"]
    assert report["clipped_joint_fraction"] > 0.
    assert report["min_signed_limit_margin_action"] < 0.
    assert report["min_bilateral_residual_headroom_action"] == 0.
    assert report["raw_peak_joint_target_rad"] == pytest.approx(float(raw.abs().max()) * .3)
    assert report["clipped_peak_joint_target_rad"] == pytest.approx(float(target.abs().max()))
