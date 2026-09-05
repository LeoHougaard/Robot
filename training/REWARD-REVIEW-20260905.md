# Reward and gait acquisition review

The epoch-2,000 policy is rejected. More time on that checkpoint is not a
justified next step. Its flat test produces 0.0008 m/s for 0.08 m/s requested,
about 0.35 rad tilt, and a rear-right foot that never lands during the forward
segment. Dense frames at 4.00-6.40 seconds show a nearly fixed pose. The
preserved epoch-500 policy also fails; it is not a working baseline.

The subsequent fixed-exploration continuation did not reach PPO. Run
`20260905T231246Z-train-2275` stalled in native `SimulationApp._start_app`.
After about eleven minutes without an epoch or GPU compute process, it was
stopped. Its console contains the captured stack; `stop_decision.json`
records the stop and correction of its stale launcher status. No new
checkpoint exists. This is an infrastructure failure, not evidence that
fixed exploration cannot work.

## What the evidence does and does not establish

The completed run has an optimization defect: Gaussian action standard
deviations grew as high as 36.4 while applied actions are clipped to [-1, 1],
and adaptive actor LR reached 0.01. Finite weights and long episodes failed
to reveal the problem. Retain the implemented ability to freeze a sane
exploration distribution. It has CPU verification but no completed PPO
comparison yet.

The current reward prefers perfect tracking to standing, and standing level
to standing tilted. Therefore a positive standing reward alone does not
explain the failed pose. Removing a constant reward offset is not a cure;
with falls and timeouts it can also change incentives to terminate.

There are nevertheless weaknesses in the learning signal:

- The diagonal-pair bonus is almost absent before useful body progress.
  There is no separate credit for completing a swing and returning the foot
  to support, nor a cost for leaving a foot airborne indefinitely.
- The attitude bonus is only 0.5 reward units per second and saturates. At
  0.35 rad error it is about 0.0011, compared with 0.5 at the target. Larger
  errors barely change this term. This does not establish causation.
- There is no explicit vertical body velocity or roll/pitch angular velocity
  cost. Position tracking alone may admit unnecessary oscillation.
- The 1.28-second PPO rollout is shorter than the 1.67-second period of the
  useful 0.6 Hz diagnostic gait. Bootstrapping can bridge rollouts; this is
  not a hard inability to learn. With gamma 0.995 and lambda 0.95, the
  geometric GAE trace scale is about 0.36 seconds. Longer credit assignment
  is a hypothesis to test, not an automatic fix.
- Independent exploration at 50 Hz must pass through filtering, slew limits
  and measured servo dynamics. Coordinated slow trajectories may be hard
  to discover through those individual action samples. The scripted
  diagnostic establishes that coordinated travel is possible in this model.

## Useful working examples

[Rudin et al.](https://arxiv.org/abs/2109.11978) report locomotion trained in
simulation and transferred to ANYmal. Their
[legged_gym implementation](https://github.com/leggedrobotics/legged_gym/blob/master/legged_gym/envs/base/legged_robot.py)
includes velocity tracking, landing-triggered air-time reward and motion
regularization. The landing trigger matters: merely holding a foot up does
not continually earn the landing bonus. However, its air-time term is
disabled below 0.1 m/s planar commands. Copying that threshold would disable
it for our 0.08 m/s delivery test and for pure turning. Its observation and
actuator assumptions also need independent adaptation.

[Walk These Ways](https://gmargo11.github.io/walk-these-ways/) demonstrates
multiple commanded gaits on a real quadruped. Its
[reward implementation](https://github.com/Improbable-AI/walk-these-ways/blob/master/go1_gym/envs/rewards/corl_rewards.py)
uses desired stance/swing to score contact forces, foot velocity and swing
height. Its [actual training configuration](https://github.com/Improbable-AI/walk-these-ways/blob/master/scripts/train.py)
enables clock observations, contact terms, clearance and foot placement;
air-time reward is zero there. Thus these are two different successful
designs, not a universal list of reward weights. The configured 2-4 Hz gait
range and Go1 foot geometry cannot be copied onto our slow linkage robot.

[Policies Modulating Trajectory Generators](https://arxiv.org/abs/1910.02812)
combines periodic trajectories with learned feedback and demonstrates
controllable forward locomotion on hardware. This offers a concrete way to
give learning an initial stride while keeping feedback corrections
trainable. A [Stanford Pupper study](https://cs230.stanford.edu/projects_fall_2021/reports/103176472.pdf)
also uses this approach. It reports better transfer with lower-frequency,
higher steps after actuator modeling, but an inefficient final gait and
limited model fidelity. That small study supports investigating actuator
bandwidth; it does not certify our fitted model or controller.

The recent [Mini Pupper 2 study](https://arxiv.org/html/2607.26434v1) further
illustrates delayed position servos. Its MLP deployment substitutes many
synthetic observations and runs at a different rate. We should not adopt
those substitutions or infer that our verified encoder readings should be
replaced. Our actor must continue to consume deployable causal sensors.

## Next experiment and reward structure

First extend the existing trajectory diagnostic to both signs of translation
and yaw, at 0.6 and 0.9 Hz, using identical physical dynamics and scoring the
actual requested command. Record motion, attitude, every foot's landings,
requested target clipping, and reward components. A reference gait must be
evaluated before using it to initialize or constrain learning. The existing
forward example has substantial lateral sway and is not acceptable for
deployment.

If that reference is useful, test gait-guided initialization or bounded
learned corrections around it as a separate experiment. A generator with
phase in the actor requires a versioned observation/runtime contract and
Pixel parity checks. Do not silently insert a clock into the 426-input actor.
Do not freeze all residual motion or claim a scripted movie is a policy.

The objective should retain signed net progress and tracking of unwanted
motion on zero-command axes. Keep the existing progress-gated diagonal prior.
Add step acquisition only with explicit accounting: credit completed,
bounded-duration swings on landing; reject contact chatter and indefinite
airborne holds; disable stepping incentives at stop. Couple useful-step
credit to directional progress over the cycle so stepping in place cannot
become the final solution. A reference trajectory can provide acquisition
guidance before that progress exists.

Use a posture cost that continues distinguishing harmful tilt, with modest
body-motion and motor-target regularization. Test component scales against
recorded complete strides, quiet standing, rocking, a parked foot, wrong-way
travel and command transitions before PPO. Do not penalize foot-link COM
velocity as if it were measured contact-point slip. Our lower-leg links can
rotate while a sole point is planted.

Any candidate still faces the fixed 21-segment flat gate, actual video
inspection, held-out mild/stress tests and original-SDF comparison. Preserve
all failed evidence. No new policy has passed, been exported or been deployed.

## Bounded diagnostic results

`gait-command-v2` completed 21 cases, each 20 seconds with 14 seconds measured.
Every case had zero resets and valid reward-component accounting. At 0.6 Hz:

| Reference command | Measured relevant speed | Mean tilt | Requested target clipping |
|---|---:|---:|---:|
| Forward +0.04 m/s | +0.0405 m/s | 0.0789 rad | 0% |
| Reverse -0.04 m/s | -0.0369 m/s | 0.0297 rad | 0% |
| Left +0.04 m/s | +0.0412 m/s | 0.0666 rad | 6.19% |
| Right -0.04 m/s | -0.0372 m/s | 0.0672 rad | 6.19% |
| Turn +0.20 rad/s | +0.1826 rad/s | 0.0440 rad | 1.89% |
| Turn -0.20 rad/s | -0.1943 rad/s | 0.0322 rad | 1.89% |

Every moving case above lands every foot. However, the +0.08 m/s reference
manages only +0.0195 m/s with 0.1901 rad tilt and 12.55% requested target
clipping. The -0.08 m/s case goes the wrong way. This shows why multiplying
a slow trajectory's stride length is not a validated speed controller.
The slow forward gait also has 0.0386 m/s mean absolute lateral motion despite
only 0.0018 m/s signed lateral drift. Net drift alone conceals substantial sway.

`gait-dataset-v1` repeated the cases and recorded observations returned before
each target selection. No extra sensor update advances history. Dataset SHA:
`5356dcd450716e3e30fb030cb99220819fddc8a3303697d94a97b6941dd93fd3`.
The first initializer refused the sideways reference because of clipping.
The next selected only standing and the unclipped +/-0.04 m/s straight strides.
It preserved the 426-input actor, random critic and epoch-zero PPO state.
After 1,500 supervised updates, action RMSE was 0.00509 on late cycles with
a two-second temporal gap. This holdout is not independent generalization.

The decisive closed-loop test, `gait-actor-v1`, failed. For +0.04 m/s, the
learned actor travels -0.0181 m/s with 0.1399 rad tilt. For -0.04 m/s, it reaches
only -0.0082 m/s and barely cycles two feet. Even the stop command produces
unwanted stepping. Actor SHA:
`145e6f5e0ef23e4ee37090dafe57f0fd446213bbde1d48f37134086af81d0a61`.
It is rejected for deployment and not selected for another long PPO run.
Low target-fitting error did not establish a stable feedback controller.
Loss of stride phase and distribution shift are hypotheses; the experiment
does not isolate their individual effects. Explicit stride state and a
bounded feedback policy remain the next architecture to investigate.

The first command diagnostic also stalled before scene creation. Its two
native threads waited on futexes, with Carbonite shared memory created during
that launch. Stale shared memory is therefore not established as the cause.
Disabling crash reporting only for the diagnostic process with
`OMNI_CRASHREPORTER_ENABLED=0` allowed the command, dataset and learned-actor
diagnostics to finish. This is a working debugging configuration over three
launches, not a proven root-cause fix. NVIDIA documents this process-local
override in the [Carbonite crash reporter guide](https://docs.omniverse.nvidia.com/kit/docs/carbonite/208.3.2/docs/CrashReporter.html).
No host service, driver, account or Docker configuration changed.
