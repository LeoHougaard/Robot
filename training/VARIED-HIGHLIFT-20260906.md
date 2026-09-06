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
