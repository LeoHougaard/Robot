"""Cross-language V21 session evidence from Torch, never a deployable policy.

Runs a minute of raw sensor replay, command smoothing, reference/residual
composition, filtering, startup hold and calibrated coupled-joint targets.
Stores sparse expected outputs while Kotlin independently runs every frame.
"""
import argparse
import copy
import hashlib
import json
import math
from pathlib import Path

import torch

from delivery_gait import combine_stride
from deployable_dynamics import gravity_estimate, motor_to_policy, policy_to_motor


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def create(sensor_path, reference_path, calibration_path, output):
    sensor = json.loads(sensor_path.read_text())
    spec = json.loads(reference_path.read_text())
    calibration = json.loads(calibration_path.read_text())
    convention = spec.get('joint_coordinate_convention', 'legacy_relative_knee')
    assert calibration.get('joint_coordinate_convention', 'legacy_relative_knee') == convention
    coupled = convention == 'legacy_relative_knee'
    assert convention in ('legacy_relative_knee', 'cad_drives_v1')
    joints = sorted(calibration['joints'], key=lambda j: j['policy_index'])
    zeros = torch.tensor([j['zero_deg'] for j in joints])
    scales = torch.tensor([j['servo_degrees_per_policy_radian'] for j in joints])
    matrix = torch.tensor(calibration['imu']['body_axis_from_sensor_axis'])
    bias = torch.tensor(sensor['gyro_bias_dps'])
    metadata = copy.deepcopy(sensor['metadata'])
    metadata.update(observation_size=428, observation_builder='current_body_v21_428',
                    profile_sha256=spec['profile_sha256'], action_semantics='stride_reference_plus_residual',
                    stride_reference_contract=dict(asset='stride_reference.json', sha256=sha(reference_path),
                        clock='float32_20ms_per_fresh_feedback', initial_hold_frames=50))
    if not coupled:
        assert all('linkage' not in joint for joint in joints)
        metadata.update(observation_builder='current_body_v22_428', joint_coordinate_convention=convention)
    posture_contract = metadata['posture_command_contract']
    for name in ('height_offset_m', 'roll_rad', 'pitch_rad'):
        posture_contract[name] = [0., 0.]
    menu = [[.04,0.,0.],[-.04,0.,0.],[0.,.02,0.],[0.,-.02,0.],[0.,0.,.1],[0.,0.,-.1],[0.,0.,0.]]
    expected = []
    elapsed = torch.zeros(1)
    command = torch.tensor([menu[0]])
    posture = torch.zeros(1,3)
    gravity = torch.zeros(1,3)
    filtered = torch.zeros(1,12)
    applied = torch.zeros(1,12)
    held = torch.zeros(12)
    current = metadata['current_observation_contract']
    cb = torch.tensor(current['normalization_bias_ma'])
    cs = torch.tensor(current['normalization_scale_ma'])
    cc = torch.tensor(current['clip_normalized'])
    limits = torch.tensor(metadata['action_contract']['applied_normalized_clip_by_joint'])
    history = None
    previous_position = None
    applied_by_sequence = {0: applied.clone()}
    checkpoints = {0,1,23,31,48,49,50,51,98,99,100,101,198,199,200,201,249,250,251,252,2999}
    checkpoints.update(range(499,3000,250))
    for index in range(3000):
        raw = sensor['frames'][index % len(sensor['frames'])]['state']
        interval = (20,19,21)[index % 3] if index else 20
        dt = interval / 1000.
        order = [raw['ids'].index(j['servo_id']) for j in joints]
        encoder = torch.tensor([raw['angles_deg'][i] for i in order])
        position = motor_to_policy((encoder-zeros)/scales, coupled)
        velocity = torch.zeros_like(position) if previous_position is None else (position-previous_position)/dt
        previous_position = position
        gyro = matrix @ (torch.tensor(raw['gyro_dps'])-bias) * (math.pi/180)
        acceleration = matrix @ torch.tensor(raw['accel_mg'])
        gravity = gravity_estimate(gravity, acceleration[None], gyro[None], dt, torch.tensor([index==0]))
        target = torch.tensor([menu[(index//250) % len(menu)]])
        command += .05 * (target-command)
        # One-frame acknowledgment lag in selected intervals. Sensor history
        # must use the acknowledged action, not the most recently calculated one.
        acknowledged = max(0,index-(1 if index % 13 == 0 else 0))
        previous_action = applied_by_sequence[acknowledged][0]
        valid = torch.zeros(12)
        for joint, at in enumerate(order):
            value = raw['current_raw'][at]
            if value is not None:
                held[joint] = ((abs(value * 6.5)-cb[joint])/cs[joint]).clamp(0,cc[joint])
                valid[joint] = 1.
        frame = torch.cat((gyro,gravity[0],command[0],position,.05*velocity,previous_action,held,valid,torch.tensor([dt/.02])))
        history = frame.repeat(24,1) if history is None else torch.cat((history[1:],frame[None]))
        angle = 2*math.pi*spec['frequency_hz']*(elapsed-spec['settle_seconds']).clamp_min(0)
        clock = torch.stack((torch.sin(angle),torch.cos(angle)),dim=-1)
        observation = torch.cat((history[metadata['history_selection']['indices']].flatten(),command[0],posture[0],clock[0]))
        # Deliberate saturations and sign changes exercise both safety bounds.
        residual = torch.tensor([[((index+7*j)%41-20)/10. for j in range(12)]])
        combined = combine_stride(residual,command,posture,gravity,elapsed,spec)
        filtered += .2*(combined.clamp(-1,1).clamp(-limits,limits)-filtered)
        next_applied = filtered.clamp(applied-.2,applied+.2)
        if index < 50:
            filtered.zero_()
            next_applied.zero_()
        applied = next_applied
        applied_by_sequence[index+1] = applied.clone()
        degrees = zeros + scales*policy_to_motor(.3*applied[0], coupled)
        assert torch.isfinite(observation).all() and torch.isfinite(degrees).all()
        if index in checkpoints:
            expected.append(dict(frame=index, elapsed_seconds=elapsed.item(), observation=observation.tolist(),
                filtered=filtered[0].tolist(),applied=applied[0].tolist(),servo_degrees=degrees.tolist()))
        elapsed += .02
    output.write_text(json.dumps(dict(kind='synthetic_stride_session_parity', frames=3000,
        generator_sha256=sha(Path(__file__)), reference_sha256=sha(reference_path),
        sensor_fixture_sha256=sha(sensor_path), calibration_sha256=sha(calibration_path),
        metadata=metadata, menu=menu, expected=expected),separators=(',',':'))+'\n')
    print(json.dumps(dict(output=str(output),bytes=output.stat().st_size,checkpoints=len(expected))))


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('sensor','reference','calibration','output'):
        parser.add_argument(name,type=Path)
    args=parser.parse_args()
    create(args.sensor,args.reference,args.calibration,args.output)
