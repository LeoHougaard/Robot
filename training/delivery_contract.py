"""Fixed V20 command screen, declared before its first PPO run (SI units)."""

import math

SEGMENTS = (
    ("stand", 150, 0., 0., 0., 0., 0., 0.),
    ("forward", 250, .08, 0., 0., 0., 0., 0.),
    ("reverse", 250, -.08, 0., 0., 0., 0., 0.),
    ("strafe_left", 250, 0., .06, 0., 0., 0., 0.),
    ("strafe_right", 250, 0., -.06, 0., 0., 0., 0.),
    ("turn_left", 250, 0., 0., .20, 0., 0., 0.),
    ("turn_right", 250, 0., 0., -.20, 0., 0., 0.),
    ("diagonal_left", 250, .06, .04, 0., 0., 0., 0.),
    ("diagonal_right", 250, .06, -.04, 0., 0., 0., 0.),
    ("diagonal_reverse_left", 250, -.06, .04, 0., 0., 0., 0.),
    ("diagonal_reverse_right", 250, -.06, -.04, 0., 0., 0., 0.),
    ("curve_left", 250, .06, .03, .15, 0., 0., 0.),
    ("curve_right", 250, .06, -.03, -.15, 0., 0., 0.),
    ("stop", 150, 0., 0., 0., 0., 0., 0.),
    ("crouch_walk", 250, .06, 0., 0., -.01, 0., 0.),
    ("tall_walk", 250, .06, 0., 0., .01, 0., 0.),
    ("roll_left_walk", 250, .06, 0., 0., 0., .06, 0.),
    ("roll_right_walk", 250, .06, 0., 0., 0., -.06, 0.),
    ("pitch_up_walk", 250, .06, 0., 0., 0., 0., .06),
    ("pitch_down_walk", 250, .06, 0., 0., 0., 0., -.06),
    ("current_dropout_walk", 250, .08, 0., 0., 0., 0., 0.),
)
HISTORY_INDICES = (0, 10, 20, 21, 22, 23)
BUILDER = "current_body_v20_426"
CONTROL_HZ = 50


def timing_feature_coverage(observed_min, observed_max, sample_count, *,
                            interval_ms, reference_ms, randomized):
    """Validate realized actor features, not just the configured stress label.

    These extrema summarize timing slots actually sent to the actor. Passing
    proves feature exposure only, never variable-rate sensor/physics coverage.
    """
    low, high = interval_ms if randomized else (reference_ms, reference_ms)
    if not (math.isfinite(reference_ms) and reference_ms > 0.
            and math.isfinite(low) and math.isfinite(high) and 0. < low <= high):
        raise ValueError("invalid timing-feature interval or reference")
    if sample_count <= 0 or not (math.isfinite(observed_min) and math.isfinite(observed_max)):
        raise RuntimeError("missing or nonfinite actor timing-feature coverage")
    expected_min, expected_max = low / reference_ms, high / reference_ms
    if not (expected_min - 1e-6 <= observed_min <= observed_max <= expected_max + 1e-6):
        raise RuntimeError("actor timing features are outside the configured bounds")
    varied = observed_max - observed_min > 1e-6
    if low != high and not varied:
        raise RuntimeError("timing-feature stress requested but actor timing input did not vary")
    return dict(model=("independent_feature_perturbation_v1" if randomized else "fixed_control_interval"),
                observed_min_normalized=observed_min, observed_max_normalized=observed_max,
                actor_timing_values_checked=sample_count, varied=varied,
                variable_sensor_sampling=False, variable_action_hold_physics=False)
