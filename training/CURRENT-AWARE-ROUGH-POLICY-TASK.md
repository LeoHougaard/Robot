# Current-aware rough-terrain policy task

Use this file as the complete task for a new visible Codex thread. Do not use a
hidden subagent. Continue until the strongest evidence-backed simulation policy
or policy set is retained. Leo alone operates the physical robot; do not start
physical motion or a hardware policy run.

## Outcome

Train a new, separately versioned policy family that:

- walks and steers reliably on held-out rough terrain;
- accepts controllable body-height/crouch and body roll/pitch commands while
  walking;
- uses synchronized servo current and validity data to learn a deployable
  ground-contact/load proxy;
- remains robust when the physical robot differs substantially from simulation;
- matches the measured servo response and transport evidence as closely as the
  available physical data supports.

Never overwrite or silently change the known V1 or V2 policy families.

## Physical evidence

Use the cleaned, provenance-bearing capture and its generated fit:

```text
pixel_robot/run_data/robot-run-20260829-195709-660-8aea13e2-trimmed-training-capture.zip
pixel_robot/run_data/robot-run-20260829-195709-660-8aea13e2-trimmed-analysis/simulation-fit.json
pixel_robot/run_data/robot-run-20260829-195709-660-8aea13e2-trimmed-analysis/run-summary.json
```

The capture retains 2,189 policy frames over 50.727 seconds, with complete
critical feedback and current measurements for all 12 servos. Read
`training/TRAINING-CURRENT-OBSERVATION.md` and `training/TRAINING-V2.md`.

Important observations:

- requested actor outputs were clipped on about 63% of frames;
- knee servos 3, 9, and 12 have especially large effective lag and tracking
  error;
- current bias, range, lag, clipping, noise, and dynamics differ by servo;
- the observed policy rate is 43.12 Hz with a 23 ms median firmware interval;
- current is a useful contact/load proxy, not calibrated ground-reaction force.

Use this run for actuator/current calibration now. Keep the 50 Hz transport
improvement as a separate deployment issue and do not claim that this recording
passes the 50 Hz gate.

## Research gate

Before implementing or training, research the smallest design that can meet the
outcome. Use current official Isaac Lab/Isaac Sim documentation and primary
research papers. Record a concise design decision with citations. Specifically
check:

- deployable current/effort observations for contact-aware quadruped control;
- actuator-network or delay/noise models appropriate for position servos;
- command-conditioned body height, roll, and pitch control;
- rough-terrain curricula and domain randomization that improve transfer;
- evaluation methods that distinguish walking from sliding or reward exploits.

Avoid observation and reward bloat. Every actor observation must exist on the
physical robot or be a user/controller command.

## Policy and simulation contract

- Start from the existing deployable V2 frame: body IMU estimate, joint
  position/velocity, planar/yaw command, and previous action history.
- Add all 12 normalized absolute servo-current measurements and 12 freshness
  validity values. The existing proposal is four 69-value frames, producing a
  276-value observation. Change it only with a documented, deployable reason.
- Add the minimum command representation needed for desired body height/crouch,
  roll, and pitch. Do not add simulator-truth position, velocity, terrain height,
  contact, or absolute heading to the actor.
- Absolute world position/heading remains the responsibility of an external
  goal/odometry controller. Do not pretend the onboard sensors provide it.
- Fit per-servo effective response, speed, lag, error, current distribution,
  clipping, noise, latency, and dropout from the complete physical capture.
- Simulated current must reproduce hardware normalization and validity behavior.
  Missing data uses validity zero and the same finite-value holding rule as the
  runtime.
- Use broad, physically valid domain randomization. Target roughly ±25% where
  appropriate for mass, inertia, actuator strength, and related parameters;
  vary center of mass materially within valid link geometry. Research and bound
  friction, restitution, latency, sensor noise, voltage/strength, and contact
  parameters rather than applying a blind ±25% rule to everything.
- Preserve the progress-gated diagonal-pair trot prior and safety/contact
  invariants unless matched evidence supports a smaller general change.
- Use rough-terrain task demands and curriculum to produce useful foot lift;
  avoid stacking redundant clearance rewards.

## Training and variants

Use the GB10 through the repository launch/status scripts. Require remote
`whoami` to be exactly `leo`, inspect every active GPU/container workload, and
do not interrupt unrelated work.

Train in bounded, reviewable stages. Evaluate flat regression, held-out rough
terrain, directional motion, stop/stand, commanded crouch/height, commanded
roll/pitch, current dropout, pushes, and randomized robot parameters. Training
reward alone never promotes a checkpoint. Inspect rollout video/contact evidence
for sliding, dragging, planted feet, wrong direction, instability, and reward
shortcuts.

If compute time and evidence allow, retain several simulation-working variants
for Leo to try rather than only one:

1. balanced current-aware rough locomotion;
2. stronger commanded body-posture tracking;
3. maximum sim-to-real robustness under the broadest passing randomization.

Prefer compatible observation and deployment contracts across variants. Each
variant must independently pass its declared deterministic simulation gates and
must have exact checkpoint, metadata, normalization, and provenance. Do not
label a merely different checkpoint as a useful policy.

## Improvement loop and completion

Establish an immutable baseline and matched evaluation first. Categorize each
failure before editing. Test one general change at a time, promote only measured
improvements, preserve the strongest prior checkpoint, and change methods if
the current approach stalls. Monitor training and visual evidence throughout;
do not stop at a plan or at the first completed PPO run.

Generated checkpoints, full captures, logs, and rollout evidence stay outside
Git. Commit and push reviewed source, tests, metadata contracts, concise research
decisions, and launcher/evaluation support. Finish with the retained best policy
or passing variants, deterministic and visual evidence, exact deployment bundle
paths, and remaining physical uncertainty.
