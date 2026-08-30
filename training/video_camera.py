"""Deterministic camera sampling for training-rollout evidence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VideoCameraSample:
    clip_index: int
    env_index: int
    view_index: int


# Body-frame offsets: forward, lateral, up.  Each clip gets a different
# quartering view as well as a robot from a different part of the terrain.
CAMERA_OFFSETS = (
    (-0.42, 0.30, 0.24),
    (-0.16, 0.46, 0.23),
    (0.28, 0.38, 0.25),
    (-0.42, -0.30, 0.24),
    (-0.16, -0.46, 0.23),
)


def stratified_env_indices(num_envs: int, sample_count: int = 5) -> tuple[int, ...]:
    """Return deterministic indices spread across the vectorized scene."""

    if num_envs < 1:
        raise ValueError("num_envs must be positive")
    count = min(num_envs, max(1, sample_count))
    if count == 1:
        return (0,)
    return tuple(round(index * (num_envs - 1) / (count - 1)) for index in range(count))


def select_video_camera_sample(
    step: int,
    interval: int,
    num_envs: int,
    sample_count: int = 5,
) -> VideoCameraSample:
    """Select the followed robot and view for the clip containing ``step``."""

    if interval < 1:
        raise ValueError("interval must be positive")
    clip_index = max(0, step) // interval
    indices = stratified_env_indices(num_envs, sample_count)
    slot = clip_index % len(indices)
    return VideoCameraSample(
        clip_index=clip_index,
        env_index=indices[slot],
        view_index=clip_index % len(CAMERA_OFFSETS),
    )
