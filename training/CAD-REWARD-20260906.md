# V22 moving-foot duration experiment

This experiment adds one bounded cost to V22 Commands. During a moving
command, each foot incurs a cost only after 1.25 seconds of continuous
contact or 1.0 seconds of continuous air time. The excess ramps over 0.25
seconds and caps at one unit per foot. The summed cost is multiplied by the
positive command progress and the experimental scale, `.25` reward units per
second per overdue foot. Stop commands retain the existing all-feet support
cost and do not use the new duration term.

The base V20/V21 configuration defaults the scale to zero, preserving their
reward behavior. V22 Acquire also remains at zero; only V22 Commands enables
the experiment. Commands, terrain, stride reference, action contract and PPO
settings are unchanged.

The coefficient is a bounded experiment parameter, not a proved optimum. The
required preflight compares legacy and experimental rewards for all-contact
stop, forward motion with one planted foot, alternating four-foot timing,
prolonged air/contact, wrong-way motion and zero-net stepping. It must also
require signed command tracking, repeated lift and landing by every moving
foot, no resets, and record residual clipping before any training decision.
