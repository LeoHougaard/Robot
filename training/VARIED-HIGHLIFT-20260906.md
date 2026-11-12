# Varied terrain and higher foot lift

Leo requested varied terrain rather than predominantly stairs, and higher
foot lift. Preserve epoch 3750 as the general walking baseline. The stair
epoch 4250 remains an unpromoted experiment because it regressed stopping
and slow turning and still stalled at the taller risers.

## Plan and verification

1. Create an explicit higher-stride fork from epoch 3750, preserving the old
   checkpoint and reference. Check the reference's joint targets, runtime
   parity, flat behavior, and measured foot rise before training.
2. Train 1000 additional epochs, from 3750 to total 4750, with 128 environments
   on the fixed varied mixture. Luna supervises roughly every five minutes.
3. Compare the original, untrained fork, and trained candidate using matched
   commands and hardware variation. Measure completed swing height as well
   as progress, balance, stops, turns, and foot returns. Inspect actual videos.

The mixture uses 64 deterministic columns: 20 flat, 16 with 2.5 to 6 mm bumps,
four gentle slopes, and 24 stairs. The slopes cover both outward directions
at approximately 1.5% and 3% grades. Stairs cover 6/10/15/20 mm risers,
150/200/250 mm treads, and ascent/descent. Terrain difficulty does not advance
automatically. Flat ground and bumps together occupy over half the mixture.

## Higher stride contract

The original stride requests 20 mm peak lift at full command and fades to
10 mm for the slow lateral and turning commands. The new reference requests
30 mm, reaching full lift at 0.02 m/s planar motion or 0.10 rad/s yaw. Lift
still fades continuously to exactly zero at zero command. The gait frequency,
diagonal phases, CAD coordinates, actuator constraints, observations, and
learned residual scale stay the same.

This changes the controller surrounding the actor, so it is an explicit
fork with a new reference hash, not a claim of identical checkpoint behavior.
The old checkpoint remains untouched. The new reference kind must be
recognized by the runtime; an older runtime must reject it. Actor weights,
normalization, and optimizer state are inherited with recorded provenance.
The reference and task must match the checkpoint contract before execution.

Requested Cartesian lift is not proof of achieved lift. The evaluator records
each sole material point's peak vertical rise since its last stance during completed
swings. This is a displacement measurement, not direct terrain clearance.
Use flat ground to compare lift without a changing support surface. On
terrain, retain the distinction between a commanded trajectory, observed
foot rise, and successful obstacle traversal.

Promotion requires retained walking behavior and measured improvement.
Neither a higher configured lift nor completing PPO is sufficient. In
particular, check the known slow-turn and stopped-support failures and
the one-environment stair cases that exposed fragility to hardware variation.

The first preflight used link origins for foot rise, which is invalid for
this linkage. Those foot-rise values are superseded. The corrected diagnostic
extracts the neutral sole point from the CAD collision mesh and transforms
it with the live link pose; body and contact-sensor ordering is checked.
The earlier runs remain useful for balance, progress, resets, and visuals.

## Completed continuation: epoch 3750 to 4750

The CurrentBody V22 high lift fork completed its bounded 1,000 epoch continuation on the GB10 in run `20260906T230448Z-train-73173` (experiment `2026-09-06_23-05-05`). It used 128 environments, the varied terrain mix (20 flat, 16 bumps at 2.5–6 mm, 4 gentle slopes, and 24 stairs spanning 6–20 mm with 150–250 mm treads), and source snapshot commit `73cea6b`. The fork preserved parent checkpoint SHA `314d6f0e8a03eb531440b249290793329325bec23371c9078f5cee0fca5368dc`. The selected epoch-4750 checkpoint is:

`/home/leo/isaac-workspace/projects/training/logs/rl_games/quadruped_current_body_v22_assembly_four_leg_linkage_12dof_highlift/2026-09-06_23-05-05/nn/last_quadruped_current_body_v22_assembly_four_leg_linkage_12dof_highlift_ep_4750_rew_297.18176.pth`

SHA256: `5b1dfa039cab30f7599e27c872cf10a30918c11e535c7257a82a812148a841d7`.

The checkpoint and run provenance are preserved outside Git at `C:\Users\Leo\Videos\Robot-policy-review-20260905\preserved-epoch4750`, including the source manifest, control profile, simulation fit, source checkpoint record, and launcher metadata. The matched post-training evaluations are recorded below; this candidate is not promoted and no installed policy bundle was changed.

The corrected sole-point measurement uses the lowest collision-sole material
point transformed by body link pose. Results are ordered by semantic foot
names `[FR, FL, BR, BL]`; the old diagnostic label `[base, LF, RR, LR]` was
incorrect for this CAD linkage.

The 20-case post queue completed. Gate failure counts were:

| Case | Baseline | High lift candidate |
|---|---:|---:|
| flat60 | 0 | 4 |
| flat timing60 | 0 | 2 |
| bumps20 | 0 | 5 |
| slope up / down | 1 / 2 | 3 / 2 |
| stairs 6 mm ascend | 12 | 6 |
| stairs 20 mm ascend | 22 | 29 |
| stairs 20 mm descend | 33 | 24 |
| video checks (bumps / flat / stairs6 / stairs20) | — | 2 / 1 / 5 / 3 |

For the nominal `.06 m/s` forward command on flat ground, corrected sole
mean rises in metres were `[0.000965, 0.001123, 0.012051, 0.003616]` for the
baseline and `[0.001571, 0.001408, 0.025255, 0.013703]` for the candidate,
ordered `[FR, FL, BR, BL]`. The candidate raises the aggregate measurement,
but that does not mean every foot improved on every command; the FL median in
this command was only `0.000084 m` in the candidate.

The candidate remains an unpromoted controller experiment. The retained
epoch-3750 checkpoint remains the baseline, and no installed policy bundle was
changed.


The PPO continuation took 3271.16 seconds. Evaluation evidence is retained at
`/home/leo/isaac-workspace/projects/training/reviews/varied-highlift-sole-20260906/post`.
All four recorded rollout contact sheets were visually reviewed: the robot
remains upright, but the stair clips show hesitation and limited traversal.
The 20 mm clip does not demonstrate a successful sustained climb. Videos and
the muted review page are under `C:\Users\Leo\Videos\Robot-policy-review-20260905\speed-20260906\latest-varied.html`.
The next behavioral issue to address is front-foot scuffing and lateral
wobble while retaining the broader terrain mix; a larger reference alone
did not produce a uniformly higher, balanced gait.
