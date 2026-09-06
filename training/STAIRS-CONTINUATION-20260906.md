# Stair continuation from epoch 3750

Leo requested continued walking practice on varied 6 to 20 mm stairs, with
the current walking version committed first. Tag
`robot-walk-epoch3750-20260906` preserves that source and the rollback manifest.
The full checkpoint and frozen source are backed up locally and on the GB10;
see `preserved/epoch3750-20260906.json`. The physical installed policy remains
epoch 2750.

## Training and verification plan

1. Verify actual vertical risers, spawn support, and coverage of every stair
   size. Measure the preserved policy on ascending and descending stairs
   before PPO, retaining failures as comparison evidence.
2. Continue the exact epoch 3750 checkpoint for 500 epochs, to total epoch
   4250, with 128 environments. Keep the actor, CAD coordinates, stride
   reference, commands, rewards, PPO settings, and hardware variation intact.
3. Luna supervises about every five minutes. Compare the resulting checkpoint
   against epoch 3750 under matched stair and flat conditions, and inspect
   recorded motion. Finishing training does not promote a policy.

The fixed mixture has 32 terrain columns: eight flat and one for each
combination of 6/10/15/20 mm risers, 150/200/250 mm treads, and ascent/descent.
This gives easier surfaces alongside the taller stairs. Every tile is 8 m
wide and starts with a 0.6 m central support platform. Held-out 175 and 225 mm
treads are available for evaluation. The generator uses deterministic column
assignment; the environment does not advance terrain difficulty automatically.

Use Isaac Lab's existing box-mesh stair generators. Regular pyramid stairs
descend when moving outward from the central spawn; inverted pyramid stairs
ascend. The [installed generator's upstream source](https://isaac-sim.github.io/IsaacLab/main/_modules/isaaclab/terrains/trimesh/mesh_terrains.html)
constructs vertical box faces. CPU checks must verify the resulting surfaces,
including riser height and the actual central platform, rather than trusting
configuration names or total mesh height.

## Known limits and interpretation

Epoch 3750 passes the recorded 20-second forward test on 2.5 to 6 mm bumps,
averaging 0.0579 m/s for a 0.06 m/s command, with no resets. It still fails a
foot-lift check during slow negative yaw in longer gentle rough evaluations.
Keep that limitation visible when comparing the stair candidate.

The existing reference requests at most 20 mm of foot lift and fades the lift
at slower commands. The policy has bounded residual joint corrections. A
20 mm riser therefore provides a demanding test of available clearance.
If video shows a persistent clearance limit, analyze the reference and action
range before spending another run on the same failure. No reference checksum
or action contract changes are part of this terrain-only continuation.

Terrain-ray observations can verify that a translating robot encountered a
height transition. A robot turning on the central flat platform has not
demonstrated stair turning. Record exposure per environment and distinguish
mesh presence from actual terrain contact. These intermediate simulator
screens do not establish physical readiness or complete stair coverage.

## Baseline measured before PPO

The frozen evaluator from commit `53d26f3` completed all four 14-environment,
20-second screens without resets or invalid terrain rays. At the 0.06 m/s
forward command, epoch 3750 averaged 0.0349 m/s ascending 6 mm steps and
0.0010 m/s ascending 20 mm steps. Descending speeds were 0.0657 and 0.0552 m/s.
The 20 mm descent had the worst balance, reaching 0.411 rad tilt in one case.
The separate 20 mm ascent video confirms sustained stepping in place at the
first riser. These are training targets, not a passing stair baseline.

Evidence is preserved in GB10 `training/reviews/stairs6-20-20260906`.
The source snapshot includes all 60 dependencies and the unchanged stride
checker. Post-training comparisons use this same snapshot and random seeds.

Run `20260906T212745Z-train-66797` started from the preserved checkpoint
under source commit `53d26f3`, with 128 environments and total target 4250.
The frozen manifest verifies the parent checkpoint SHA256, actor/critic
contract, and unchanged robot/profile/fit. Actual advancement beyond epoch
3750 was verified before handing routine supervision to Luna.

The run completed epoch 4250 in 1599.59 seconds of PPO without a traceback.
Its periodic checkpoint is
`logs/rl_games/quadruped_current_body_v22_assembly_four_leg_linkage_12dof/2026-09-06_21-28-01/nn/last_quadruped_current_body_v22_assembly_four_leg_linkage_12dof_ep_4250_rew_258.2219.pth`,
SHA256 `8c451c8c3590be3d8291348a1e9d1a9cb69b0d580cb8144d443c4f0af0d382b2`.
Post-PPO evidence goes in `training/reviews/stairs6-20-20260906-postppo`.
The candidate is not promoted. The preserved epoch 3750 remains the preferred
general walking checkpoint, and the physical app remains unchanged.

## Matched results

Both checkpoints used the same frozen evaluator, seed, commands, and physical
variation. Each stair screen ran 14 environments for 20 seconds. No resets
occurred. The table uses command index 8, requesting 0.06 m/s forward.

| Terrain | Epoch 3750 speed | Epoch 4250 speed |
| --- | ---: | ---: |
| 6 mm ascent | 0.0349 m/s | 0.0569 m/s |
| 6 mm descent | 0.0657 m/s | 0.0656 m/s |
| 20 mm ascent | 0.0010 m/s | 0.0022 m/s |
| 20 mm descent | 0.0552 m/s | 0.0550 m/s |

Mean tilt across the 14 descent cases improved from 0.0500 to 0.0446 rad on
6 mm stairs, and from 0.1125 to 0.1006 rad on 20 mm stairs. The separate
20 mm forward video still averages only 0.0024 m/s and fails progress.
The candidate learned useful smaller-step traversal, but it did not solve
the 20 mm ascent.

Both candidates pass the separate 2.5 to 6 mm bump-forward check. Epoch 3750
also passes both 60-second flat screens. Epoch 4250 fails stopped support
for feet 1 and 2 in the nominal screen, and foot 3 landing/lift checks during
slow negative yaw in the timing-variation screen. Those regressions prevent
replacing the saved general walking policy. The full stair screens still
fail intermediate progress, balance, or foot-contact checks as well.

Before a further continuation, investigate actual swing clearance and
available residual correction at the first 20 mm riser, and address the
stop/turn regressions. The data does not justify assuming that additional
epochs alone will solve them. Keep the candidate and all failed evidence
for comparison; do not change the physical policy on the strength of the
improved 6 mm forward result.

Visual review of the separate one-environment video found a further limit:
with its different sampled hardware variation, the candidate also stalls
on 6 mm ascents, averaging 0.0028 m/s despite repeated foot landings. Its
20 mm video averages 0.0024 m/s. Both clips were inspected through their
full contact sheets. The multi-environment 6 mm improvement therefore does
not establish robust smaller-stair walking. Preserve both views of the
result rather than selecting only the favorable hardware sample.
