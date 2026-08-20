"""Fail-closed planar goal controller for the locomotion policy command."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan2, cos, pi, sqrt


def wrap_angle(angle: float) -> float:
    """Wrap radians to [-pi, pi)."""

    return (angle + pi) % (2.0 * pi) - pi


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


@dataclass(frozen=True)
class Pose2D:
    x: float
    y: float
    yaw: float


@dataclass(frozen=True)
class BodyCommand:
    forward: float
    lateral: float
    yaw_rate: float
    reached: bool


@dataclass(frozen=True)
class GoalControllerConfig:
    max_forward_mps: float = 0.18
    max_yaw_rate_rps: float = 0.25
    distance_gain: float = 0.8
    heading_gain: float = 1.2
    position_tolerance_m: float = 0.08
    heading_tolerance_rad: float = 0.12
    rotate_first_rad: float = 0.70


class GoalController:
    """Convert a world-frame x/y/yaw goal into deployable body commands."""

    def __init__(self, config: GoalControllerConfig | None = None):
        self.config = config or GoalControllerConfig()

    def command(self, pose: Pose2D, goal: Pose2D) -> BodyCommand:
        cfg = self.config
        dx = goal.x - pose.x
        dy = goal.y - pose.y
        distance = sqrt(dx * dx + dy * dy)

        if distance <= cfg.position_tolerance_m:
            yaw_error = wrap_angle(goal.yaw - pose.yaw)
            if abs(yaw_error) <= cfg.heading_tolerance_rad:
                return BodyCommand(0.0, 0.0, 0.0, True)
            return BodyCommand(
                0.0,
                0.0,
                clamp(
                    cfg.heading_gain * yaw_error,
                    -cfg.max_yaw_rate_rps,
                    cfg.max_yaw_rate_rps,
                ),
                False,
            )

        travel_heading = atan2(dy, dx)
        heading_error = wrap_angle(travel_heading - pose.yaw)
        yaw_rate = clamp(
            cfg.heading_gain * heading_error,
            -cfg.max_yaw_rate_rps,
            cfg.max_yaw_rate_rps,
        )
        if abs(heading_error) >= cfg.rotate_first_rad:
            return BodyCommand(0.0, 0.0, yaw_rate, False)

        forward = min(cfg.max_forward_mps, cfg.distance_gain * distance)
        forward *= max(0.0, cos(heading_error))
        return BodyCommand(forward, 0.0, yaw_rate, False)
