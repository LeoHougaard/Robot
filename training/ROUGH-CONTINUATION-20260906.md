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
Both stationary-start checks pass. The first one-eighth-height uniform terrain
screen passes all fourteen commands with finite terrain rays. Slope checks
and a rough video are pending. No rough PPO run has started at this entry.
