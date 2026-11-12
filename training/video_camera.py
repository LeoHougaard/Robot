"""Deterministic camera sampling for training-rollout evidence."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class VideoCameraSample:
    clip_index: int
    env_index: int
    view_index: int


# Body-frame offsets: forward, lateral, up.  Keep the eye close enough that
# the small linkage robot remains readable in the shared-browser preview while
# retaining enough rough terrain to judge progress and foot contact.
CAMERA_OFFSETS = (
    (-0.55, 0.45, 0.32),
    (-0.20, 0.65, 0.32),
    (0.50, 0.45, 0.32),
    (-0.55, -0.45, 0.32),
    (-0.20, -0.65, 0.32),
)


def update_recording_camera(recorder, eye, target):
    """Update the actual Isaac Lab 3 recording camera, including first capture.

    SimulationContext.set_camera_view only updates visualizers. The installed
    Kit recorder uses a separate camera and initializes it from its own cfg.
    Keep this version-specific adapter here instead of patching Isaac Lab.
    """
    capture = getattr(recorder, "_capture", None)
    if capture is None:
        raise RuntimeError("Video requested without an initialized recording backend")
    capture.cfg.eye = tuple(eye)
    capture.cfg.lookat = tuple(target)
    if hasattr(capture, "update_camera"):
        capture.update_camera(eye, target)
    else:
        from isaacsim.core.rendering_manager import ViewportManager

        ViewportManager.set_camera_view(
            capture.cfg.camera_prim_path, eye=list(eye), target=list(target)
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
