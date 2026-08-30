"""Success gates shared by the rough-terrain curriculum and local tests."""

from __future__ import annotations


def classify_terrain_progress(
    completed_steps,
    max_episode_length,
    commanded_distance,
    tracked_distance,
    terrain_size,
):
    """Return promote and demote masks from cumulative command tracking.

    The arguments may be Python scalars or tensor-like values supporting
    comparisons and boolean ``&``.  Tracking is accumulated along the command
    active at each step, so changing pose-goal directions do not invalidate the
    episode as they did when only final displacement was inspected.
    """

    valid_episode = completed_steps > 0.25 * max_episode_length
    enough_to_promote = commanded_distance > 0.15 * terrain_size
    enough_to_demote = commanded_distance > 0.10 * terrain_size
    move_up = (
        valid_episode
        & enough_to_promote
        & (tracked_distance >= 0.70 * commanded_distance)
    )
    not_move_up = (not move_up) if isinstance(move_up, bool) else ~move_up
    move_down = (
        valid_episode
        & enough_to_demote
        & (tracked_distance < 0.50 * commanded_distance)
        & not_move_up
    )
    return move_up, move_down


def base_contact_is_terminal(*, suppress_base_contact_termination: bool) -> bool:
    """Keep fall reward accounting aligned with the termination contract."""

    return not suppress_base_contact_termination


def staged_command_thresholds(
    *,
    turn_fraction: float,
    stand_fraction: float,
    reverse_fraction: float,
    lateral_fraction: float,
    diagonal_fraction: float,
) -> tuple[float, float, float, float, float]:
    """Build non-overlapping categorical-command thresholds.

    The unused probability mass remains the familiar forward/curve command.
    Keeping the staged modes categorical prevents a uniform x/y/yaw sample
    from starving pure reverse, strafe, or turn commands during continuation.
    """

    fractions = (
        turn_fraction,
        stand_fraction,
        reverse_fraction,
        lateral_fraction,
        diagonal_fraction,
    )
    if any(fraction < 0.0 or fraction > 1.0 for fraction in fractions):
        raise ValueError("Command-mode fractions must be between zero and one.")
    if sum(fractions) > 1.0 + 1.0e-9:
        raise ValueError("Command-mode fractions must sum to at most one.")

    thresholds = []
    cumulative = 0.0
    for fraction in fractions:
        cumulative += fraction
        thresholds.append(cumulative)
    return tuple(thresholds)
