"""Fixed V20 command screen, declared before its first PPO run (SI units)."""

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
