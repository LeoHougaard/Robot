"""Create small synthetic raw-sensor fixtures from the training tensor math.

Includes encoder quantization in the firmware's reported degrees, clock wrap,
irregular sample intervals, gyro rotation, dynamic acceleration and current loss.
This generates test data only; it cannot communicate with a robot.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import numpy as np
import torch
from current_policy_fit import load_current_policy_fit
from delivery_contract import HISTORY_INDICES, BUILDER
from deployable_dynamics import gravity_estimate, motor_to_policy, policy_to_motor


def create(calibration_path, fit_path, output):
    calibration = json.loads(calibration_path.read_text())
    joints = sorted(calibration["joints"], key=lambda j: j["policy_index"])
    fit = load_current_policy_fit(fit_path, [j["semantic"] for j in joints], control_hz=50)
    zeros = torch.tensor([j["zero_deg"] for j in joints])
    scales = torch.tensor([j["servo_degrees_per_policy_radian"] for j in joints])
    matrix = torch.tensor(calibration["imu"]["body_axis_from_sensor_axis"])
    bias = torch.tensor(calibration["imu"]["gyro_bias_dps"])
    metadata = dict(profile_id=calibration["robot"], profile_sha256="0"*64, weights_sha256="0"*64,
                    control_hz=50, observation_size=426, observation_history=24, observation_builder=BUILDER,
                    action_size=12, command_smoothing_time_s=.4,
                    history_selection=dict(frame_size=70, indices=list(HISTORY_INDICES), timing_reference_ms=20.),
                    current_observation_contract=dict(units="mA", absolute=True, current_step_ma=6.5,
                        normalization_bias_ma=list(fit.current_bias_ma), normalization_scale_ma=list(fit.current_scale_ma),
                        clip_normalized=[c/s for c,s in zip(fit.current_clip_ma,fit.current_scale_ma)],
                        missing_behavior="hold_last_finite_and_validity_zero"),
                    posture_command_contract=dict(height_offset_m=[-.01,.01],roll_rad=[-.06,.06],pitch_rad=[-.06,.06],
                        smoothing_time_s=.5,layout="append_after_selected_history"),
                    validated_command_limits=dict(forward_m_s=[-.08,.08],lateral_m_s=[-.06,.06],yaw_rate_rad_s=[-.2,.2]),
                    action_contract=dict(actor_output_clip=[-1.,1.],applied_normalized_clip_by_joint=[.4,1.,1.]*4,
                        low_pass_alpha=.2,applied_normalized_slew_limit=.2,position_target_scale_rad=.3),
                    stationary_action_contract=dict(behavior="policy_stabilization",normalized_stance_action=[0.]*12,
                        planar_command_deadband_m_s=0.,yaw_command_deadband_rad_s=0.))
    frames, history = [], []
    previous_gravity = torch.zeros(1,3)
    previous_q = None
    held = np.zeros(12)
    tick = 2**32-200
    for index in range(32):
        interval = (20,19,21)[index%3] if index else 20
        tick = (tick+interval) % 2**32
        dt = interval/1000
        q = torch.sin(torch.arange(12)*.4+index*.08)*.08
        raw_encoder = torch.round((zeros + scales*policy_to_motor(q))*4095/360)
        reported_degrees = raw_encoder*360/4095
        q = motor_to_policy((reported_degrees-zeros)/scales)
        velocity = torch.zeros(12) if previous_q is None else (q-previous_q)/dt
        previous_q = q
        accel_sensor = torch.tensor([40.*math.sin(index*.2),120.*math.cos(index*.1),1400. if 10<=index<14 else 990.])
        gyro_sensor = bias+torch.tensor([5.*math.sin(index*.1),-3.,12.])
        gyro = matrix@(gyro_sensor-bias)*(math.pi/180)
        gravity = gravity_estimate(previous_gravity, (matrix@accel_sensor)[None], gyro[None], dt, torch.tensor([index==0]))
        previous_gravity = gravity
        command = [.06, .02*math.sin(index*.1), .1]
        posture = [.005, -.03, .02]
        previous_action = (torch.sin(torch.arange(12)*.2+index*.1)*.15).tolist()
        current = [None if (index+i)%5==0 else (-1)**i*(10+index+i) for i in range(12)]
        valid = []
        for i, raw in enumerate(current):
            valid.append(float(raw is not None))
            if raw is not None:
                held[i] = np.clip((abs(raw*6.5)-fit.current_bias_ma[i])/fit.current_scale_ma[i],
                                  0,fit.current_clip_ma[i]/fit.current_scale_ma[i])
        frame = gyro.tolist()+gravity[0].tolist()+command+q.tolist()+(.05*velocity).tolist()+previous_action+held.tolist()+valid+[dt/.02]
        history = [frame]*24 if not history else history[1:]+[frame]
        observation = [v for slot in HISTORY_INDICES for v in history[slot]]+command+posture
        # Reverse raw bus order to exercise the actual ID mapping, not array position.
        record = dict(state=dict(sample_ms=tick, ids=[j["servo_id"] for j in joints][::-1],
                                 angles_deg=reported_degrees.tolist()[::-1], gyro_dps=gyro_sensor.tolist(),
                                 accel_mg=accel_sensor.tolist(), current_raw=current[::-1]),
                      command=command, posture=posture, previous_action=previous_action, dt=dt)
        if index in (0,23,31):
            record["expected_observation"] = observation
        frames.append(record)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(dict(calibration_sha256=hashlib.sha256(calibration_path.read_bytes()).hexdigest(),
                                     gyro_bias_dps=bias.tolist(), metadata=metadata, frames=frames), separators=(",",":"))+"\n")
    print(output, output.stat().st_size)


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("calibration",type=Path)
    parser.add_argument("fit",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    create(args.calibration,args.fit,args.output)
