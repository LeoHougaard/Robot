# Rough continuation from the first walking policy

Leo reviewed the epoch 3250 forward video and requested rough terrain next.
Preserve the installed epoch 2750 policy and both flat checkpoints. This is
simulation training, with no phone deployment or physical motor operation.

## Plan and acceptance

1. Finish matched flat physical-variation, sensor-age and stationary-start
   comparisons before choosing the starting checkpoint.
2. Validate actual generated terrain, spawn clearance and finite height rays.
   Start at one-eighth terrain height, with flat ground in the training mix.
3. Evaluate explicit uneven, upward-slope and downward-slope terrain separately.
   Each screen covers all fourteen slow and faster command rows. A mixed
   terrain screen can accidentally put every evaluated robot on a flat tile.
4. Train a bounded continuation at the first justified difficulty. Keep the
   existing linkage coordinates, stride prior, action limits and rewards.
   Restore Sustained physical randomization and long command holds.
5. Compare candidate and starting checkpoint on matching terrain and flat
   ground. Inspect a full rollout video. Advance terrain height only after
   directional progress, balance, support and repeated stepping pass.

The existing stride checks are intermediate curriculum checks. Passing them
does not establish a physical rough-terrain deployment gate. Height is already
measured relative to terrain rays beneath the body. The actor receives no
terrain map. Sensor-age variation still does not simulate variable durations
between physical motor commands.

## Why this curriculum

[Rudin et al.](https://arxiv.org/abs/2109.11978) demonstrate a performance-based
terrain curriculum for quadrupedal locomotion. Their
[reference implementation](https://github.com/leggedrobotics/legged_gym/blob/master/legged_gym/envs/base/legged_robot.py)
raises difficulty after sufficient travel and lowers it after insufficient
progress. This supports adapting difficulty to demonstrated ability. The
one-eighth starting height and this robot's thresholds are project choices,
not settings validated for this robot by that paper.

Scale actual mesh and spawn-origin Z together, retaining horizontal geometry.
The uniform generator's difficulty argument does not itself compress its
height. At one-eighth scale the configured 1 to 6 mm unevenness is only
0.125 to 0.75 mm. The starting policy may already handle it. Measure before
spending a full training run on a solved level.

## Current evidence

Epoch 3250 and epoch 2750 both passed the nominal eight-command and
fourteen-command screens at 20 and 60 seconds. The changes are mixed:
epoch 3250 improves several turning and backward balance measurements, while
positive lateral motion has more mean tilt. These results do not establish
an overall improvement or a newly acquired 50 percent speed increase, because
the baseline already passes the faster commands.

The epoch 3250 forward video covers a complete minute, averaging 0.0663 m/s
for a 0.06 m/s command, with zero resets and repeated landings from every foot.
Visual review found sustained stepping without an obvious collapse. Both
checkpoints fail the same two-foot lift/landing checks in one randomized
fast-forward condition. Candidate timing has one fewer failure than baseline.
This shared limitation remains an evaluation target for the rough stage.
Both stationary-start checks pass. All three one-eighth-height terrain screens
pass fourteen commands with finite terrain rays. Slope rays change during the
rollout, confirming that translating robots leave the central flat platform.

The separate single-environment forward video fails tracking and progress,
plus foot 0 and foot 3 lift/landing checks. It averages 0.0193 m/s for a
0.06 m/s command, with landings [0, 9, 10, 1] and no resets. Visual review
confirms persistent planted feet. Its randomized hardware sample differs
from the fourteen-environment screen, so the failure cannot be attributed
to terrain alone. Preserve this exact case for the post-training comparison.

Continue epoch 3250 for 500 additional epochs on the fixed one-eighth mix.
Although the short multi-environment terrain screens pass, long holds and
restored hardware variation at the faster commands remain training targets.
This changes terrain and training exposure together. No automatic terrain
advancement or physical promotion follows from finishing PPO.

Preflight evidence is in the GB10 review directory
`training/reviews/20260906-speed/20260906T170329Z-train-51934-rough-v2-eval`.
The evaluator SHA256 is
`6209945809bda16a1c4dd896c8327b9a4d0ff1fde616647ac038262d62ff7f3a`.
Its legacy `limitation` prose incorrectly describes rough results as flat with
no timing variation. The actual `terrain`, `rough_terrain_provenance` and
`variation_config` fields record the applied rough terrain and 18 to 39 ms
sensor-age bounds correctly. Physics still advances at 20 ms per policy step.

## Started run

Run `20260906T185023Z-train-58941` continues the exact epoch 3250 checkpoint
with SHA256 `744024c2f692f8d4778d81c487e759426fd2820cc2214f067a4146aa51937668`.
The frozen source comes from commit `63a2b09`. Actual epoch advancement was
verified after restoring the 428-observation actor and 438-value critic.
The target is epoch 3750 with 128 environments. Output is the V22 experiment
`2026-09-06_18-50-39`. Luna supervises approximately every five minutes.

Compare the exact failing single-environment video case and all three rough
terrain types against epoch 3250. Longer flat nominal and randomized timing
checks must accompany the terrain comparisons. Preserve all failures and
keep height fixed until the intermediate gates support advancement.

## Leo's minimum bump height

Leo requested bumps at least 2.5 mm high. The next stage uses positive
uneven-terrain heights of 2.5 to 6 mm above the tile base, with the existing
zero-height borders and flat tiles. Slopes retain their gentle one-eighth
scale independently. Flat terrain remains half of the training mixture.

Generate integer heightfield samples from 5 to 12 mm at the existing 1 mm
resolution, then scale the resulting mesh and spawn-origin Z by one-half.
CPU verification in the installed Isaac runtime found positive mesh vertices
from 0.00249999994 to 0.00600000005 m. Sloped transition faces interpolate
between samples; the minimum refers to the raised samples, not every point
on the transition from a zero-height border.

Keep the active Rough125 run and its frozen evaluation source intact. Its
sub-millimetre unevenness does not meet this new size target. A separate
RoughBumps25 stage records the larger bumps explicitly. It needs its own
rollout and comparisons before any policy can be called successful there.
