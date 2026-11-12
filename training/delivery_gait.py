"""Persistent stride reference plus bounded neural feedback, before actuators.

Geometry is fixed calibration data. Inputs are elapsed control time, command,
posture request and the causal body-IMU gravity estimate. No simulator state
is used. The ordinary joint bounds, filtering and servo model follow this.
"""
import math

import torch


def stride_reference(command, posture, gravity, elapsed, specification):
    device, dtype = command.device, command.dtype
    nominal = torch.as_tensor(specification["nominal_feet_m"], device=device, dtype=dtype)
    inverse = torch.as_tensor(specification["inverse_jacobians"], device=device, dtype=dtype)
    offsets = torch.as_tensor(specification["phase_offsets"], device=device, dtype=dtype)
    frequency = specification["frequency_hz"]
    duty = specification["duty_fraction"]
    active_time = (elapsed - specification["settle_seconds"]).clamp_min(0.)
    phase = (active_time[:, None] * frequency + offsets) % 1.
    swing = ((phase - duty) / (1. - duty)).clamp(0., 1.)
    smooth = swing.square() * (3. - 2. * swing)
    endpoint_slope = swing - 3. * swing.square() + 2. * swing.pow(3)
    vx = command[:, 0:1] - command[:, 2:3] * nominal[:, 1]
    vy = command[:, 1:2] + command[:, 2:3] * nominal[:, 0]

    def horizontal(velocity):
        amplitude = velocity * duty / frequency / 2.
        returning = (-amplitude + 2. * amplitude * smooth
                     - velocity * (1. - duty) / frequency * endpoint_slope)
        return torch.where(phase < duty, amplitude * (1. - 2. * phase / duty), returning)

    # Lift fades with the motion command. V2 references carry their own full
    # lift speeds; legacy references retain the calibrated V1 defaults.
    if specification.get("kind") == "stride_reference_cad_v2":
        planar_speed = specification["lift_full_planar_speed_m_s"]
        yaw_rate = specification["lift_full_yaw_rate_rad_s"]
    else:
        planar_speed, yaw_rate = .04, .2
    moving = (torch.linalg.vector_norm(command[:, :2], dim=-1) / planar_speed
              + command[:, 2].abs() / yaw_rate).clamp(0., 1.)[:, None]
    z = torch.where(phase < duty, 0., specification["lift_m"] * torch.sin(math.pi * swing).square()) * moving
    desired_gravity = torch.stack((-torch.sin(posture[:, 2]),
                                   torch.sin(posture[:, 1]) * torch.cos(posture[:, 2]),
                                   -torch.cos(posture[:, 1]) * torch.cos(posture[:, 2])), dim=-1)
    def plane(normal):
        return (-nominal[:, 2] - normal[:, :2] @ nominal[:, :2].T) / normal[:, 2:3].clamp(max=-.5)
    correction = ((plane(gravity) - plane(desired_gravity)) * specification["attitude_gain"]).clamp(
        -specification["maximum_attitude_correction_m"], specification["maximum_attitude_correction_m"])
    ramp = (active_time / specification["ramp_seconds"]).clamp(0., 1.)[:, None, None]
    displacement = torch.stack((horizontal(vx), horizontal(vy), z + correction - posture[:, 0:1]), dim=-1) * ramp
    return torch.einsum("lij,nlj->nli", inverse, displacement).flatten(1) / specification["position_target_scale_rad"]


def combine_stride(residual, command, posture, gravity, elapsed, specification):
    return (stride_reference(command, posture, gravity, elapsed, specification)
            + specification["residual_scale"] * residual.clamp(-1., 1.))
