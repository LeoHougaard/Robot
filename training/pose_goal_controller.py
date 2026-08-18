"""Deployable planar pose-goal controller for the locomotion policy.

The learned actor continues to consume body-frame forward, lateral, and yaw
velocity commands.  This controller converts a body-frame ``x, y, yaw`` pose
error into that existing command contract, so the same logic can run above the
policy on the physical robot when an odometry or localization estimate exists.
"""

from __future__ import annotations

import torch


def wrap_to_pi(angle: torch.Tensor) -> torch.Tensor:
    """Wrap angles to ``[-pi, pi]`` without a discontinuous remainder branch."""

    return torch.atan2(torch.sin(angle), torch.cos(angle))


def pose_error_to_velocity_command(
    position_error_b: torch.Tensor,
    heading_error_b: torch.Tensor,
    *,
    max_forward_speed: float,
    max_reverse_speed: float,
    max_lateral_speed: float,
    max_yaw_rate: float,
    position_tolerance: float,
    heading_tolerance: float,
    distance_gain: float,
    final_heading_gain: float,
) -> torch.Tensor:
    """Convert body-frame pose error into ``forward, lateral, yaw-rate``.

    Position error drives body-frame forward/reverse and lateral motion while
    heading error drives rotation.  All three axes taper continuously near the
    goal, allowing the same controller to exercise and deploy omnidirectional
    locomotion instead of hiding reverse/strafe behind a turn-then-walk path.
    """

    if position_error_b.ndim != 2 or position_error_b.shape[1] != 2:
        raise ValueError("position_error_b must have shape (N, 2)")
    if heading_error_b.ndim != 1 or heading_error_b.shape[0] != position_error_b.shape[0]:
        raise ValueError("heading_error_b must have shape (N,)")

    distance = torch.linalg.vector_norm(position_error_b, dim=1)
    outside_position = distance > position_tolerance
    planar = distance_gain * position_error_b
    planar_x = torch.clamp(
        planar[:, 0], -max_reverse_speed, max_forward_speed
    )
    planar_y = torch.clamp(
        planar[:, 1], -max_lateral_speed, max_lateral_speed
    )
    planar_x = torch.where(outside_position, planar_x, torch.zeros_like(planar_x))
    planar_y = torch.where(outside_position, planar_y, torch.zeros_like(planar_y))

    yaw_rate = torch.clamp(
        final_heading_gain * wrap_to_pi(heading_error_b),
        -max_yaw_rate,
        max_yaw_rate,
    )
    yaw_rate = torch.where(
        torch.abs(heading_error_b) > heading_tolerance,
        yaw_rate,
        torch.zeros_like(yaw_rate),
    )

    command = torch.zeros(
        position_error_b.shape[0], 3,
        device=position_error_b.device,
        dtype=position_error_b.dtype,
    )
    command[:, 0] = planar_x
    command[:, 1] = planar_y
    command[:, 2] = yaw_rate
    return command
