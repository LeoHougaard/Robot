# V22 moving-foot duration experiment

This experiment adds one bounded cost to V22 Commands. During a moving
command, each foot incurs a cost only after 1.25 seconds of continuous
contact or 1.0 seconds of continuous air time. The excess ramps over 0.25
seconds and caps at one unit per foot. The summed cost is multiplied by the
positive command progress and the experimental scale, `.4` reward units per
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

The first `.25` trial reproduced all physical/contact rows and windows, but
was too weak for the measured negative-turn failure. Epoch 750's mean reward
remained 0.094659 with two poorly cycling feet, above epoch 250's 0.090818.
The linear penalty at `.4` predicts 0.090234 versus 0.090701 on those exact
fixed trajectories. Positive-strafe ordering also favors the cycling actor.
This motivates one `.4` trial; it does not predict the retrained behavior.
Evidence: `reviews/20260905-delivery/cad-duration-preflight-v1`.

Initial test claims were overstated. The first duration tests used a constant
gait stub, and a later purported diagonal example gave the same feet positive
air and contact times. Those attempts remain in history. Corrected tests use
mutually exclusive contact/air over complete 0.6 Hz cycles and actual diagonal
pairs, plus preserved rollout reward deltas. The old reward method is checked
separately for scale-zero parity. Forward tilt remains an independent failure
to monitor; duration costs alone are not assumed to resolve it.
