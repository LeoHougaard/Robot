"""Pure schedule helpers for the CurrentBodyV4 fixed difficulty ramp."""

from __future__ import annotations


def difficulty_fraction(step: int, full_difficulty_step: int, floor: float) -> float:
    """Return a bounded linear difficulty that is nonzero from step one."""

    if full_difficulty_step <= 0:
        raise ValueError("full_difficulty_step must be positive")
    if not 0.0 < floor <= 1.0:
        raise ValueError("floor must be within (0, 1]")
    progress = min(max(int(step), 0) / full_difficulty_step, 1.0)
    return floor + (1.0 - floor) * progress


def scheduled_terrain_level(
    step: int, full_difficulty_step: int, terrain_rows: int, floor: float
) -> int:
    """Select a deterministic terrain row and reach the last row on schedule."""

    if full_difficulty_step <= 0:
        raise ValueError("full_difficulty_step must be positive")
    if terrain_rows <= 0:
        raise ValueError("terrain_rows must be positive")
    difficulty = difficulty_fraction(step, full_difficulty_step, floor)
    return min(int(difficulty * terrain_rows), terrain_rows - 1)
