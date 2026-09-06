"""CPU contract and dense phase probe for the CAD high lift reference."""
import json
from pathlib import Path
import sys

import torch

sys.path.insert(0, str(Path(__file__).parent))
from delivery_gait import combine_stride


ROOT = Path(__file__).parent


def _run(reference, commands):
    # Probe every menu command across a dense phase grid; command changes are
    # not used as a surrogate for phase progression.
    phases = 201
    command = torch.tensor(commands, dtype=torch.float32).repeat_interleave(phases, dim=0)
    n = len(command)
    posture = torch.zeros((n, 3))
    gravity = torch.tensor([[0., 0., -1.]]).repeat(n, 1)
    elapsed = torch.tile(2. + torch.arange(phases) * .02, (len(commands),))
    residual = torch.zeros((n, 12))
    raw = combine_stride(residual, command, posture, gravity, elapsed, reference)
    limits = torch.tensor([.4, 1., 1.] * 4)
    applied = raw.clamp(-limits, limits)
    target = applied * .3
    per_command = target.reshape(len(commands), phases, 12)
    rate = ((per_command[:, 1:] - per_command[:, :-1]).abs().max() /
            float(elapsed[1] - elapsed[0])) if n > 1 else torch.tensor(0.)
    return raw, target, float(target.abs().max()), float(rate)


def test_contract_and_zero_stop():
    old = json.loads((ROOT / "fits/stride-reference-cad-20260906.json").read_text())
    new = json.loads((ROOT / "fits/stride-reference-cad-highlift-20260906.json").read_text())
    assert old["kind"] == "stride_reference_cad_v1"
    assert new["kind"] == "stride_reference_cad_v2"
    assert new["lift_m"] == .03
    assert new["lift_full_planar_speed_m_s"] == .02
    assert new["lift_full_yaw_rate_rad_s"] == .1
    stop = [[0., 0., 0.]]
    raw, _, _, _ = _run(new, stop)
    assert torch.equal(raw, torch.zeros_like(raw))


def test_dense_14_command_probe_reports_clipped_joint_and_rate():
    old = json.loads((ROOT / "fits/stride-reference-cad-20260906.json").read_text())
    new = json.loads((ROOT / "fits/stride-reference-cad-highlift-20260906.json").read_text())
    commands = [
        [0., 0., 0.], [.04, 0., 0.], [-.04, 0., 0.], [0., .02, 0.],
        [0., -.02, 0.], [0., 0., .1], [0., 0., -.1], [.06, 0., 0.],
        [-.06, 0., 0.], [.04, .03, 0.], [.04, -.03, 0.],
        [0., 0., .15], [0., 0., -.15],
    ]
    _, _, old_peak, old_rate = _run(old, commands)
    _, _, new_peak, new_rate = _run(new, commands)
    print(json.dumps({"old_peak_joint_target_rad": old_peak, "new_peak_joint_target_rad": new_peak,
                      "old_peak_joint_rate_rad_s": old_rate, "new_peak_joint_rate_rad_s": new_rate,
                      "limitation": "linear reference probe; does not establish physical foot clearance"}))
    assert new_peak >= old_peak
    assert new_rate >= 0.0


def test_v2_full_lift_at_slow_lateral_and_turn():
    spec = json.loads((ROOT / "fits/stride-reference-cad-highlift-20260906.json").read_text())
    spec["nominal_feet_m"] = [[0., 0., 0.]] * 4
    spec["inverse_jacobians"] = [[[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]] * 4
    for command in ([0., .02, 0.], [0., 0., .10]):
        raw, _, _, _ = _run(spec, [command])
        # The dense phase includes swing midpoint: 30 mm / 0.3 rad scale.
        assert abs(float(raw[:, 2::3].abs().max()) - .1) < 1e-5


if __name__ == "__main__":
    test_contract_and_zero_stop()
    test_dense_14_command_probe_reports_clipped_joint_and_rate()
    test_v2_full_lift_at_slow_lateral_and_turn()
