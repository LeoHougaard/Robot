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
