# Current-aware rough policy results

Date: 2026-08-30

## Decision

No CurrentV3 checkpoint passed the deterministic flat promotion screen, so no
checkpoint was promoted to rough-terrain, posture, stress, export, or physical
deployment. The strongest retained simulation baseline is the CurrentV3 Core
epoch-250 checkpoint below. It has real, visually verified four-foot forward
locomotion, but it is explicitly **not deployable** and does not meet the task's
directional or rough-terrain outcome.

The implementation, fitted physical contract, launch/evaluation support, and
failure evidence are retained. V1 and V2 bundles were not overwritten.

## Physical-data contract

- Training capture: 2,189 complete policy frames, 50.727 seconds, 43.13 Hz.
- Fit SHA-256:
  `8ec33a2eeac801bfe24471eb46b6ebdb5d8415e8ef48385c691ecd72728c5648`.
- Control-profile SHA-256:
  `75c617c31052fc60e9cf04ebcb59424a054bd5c25d4c031d409a4837350db415`.
- Actor observation: four deployable 69-value frames plus height, roll, and
  pitch commands (279 values total).
- The capture fails the separate 50 Hz transport gate; this work does not claim
  otherwise.

Design and research rationale are in
`training/CURRENT-AWARE-ROUGH-DESIGN.md`.

## Retained strongest baseline

Checkpoint:

```text
/workspace/projects/training/logs/rl_games/
quadruped_current_v3_assembly_four_leg_linkage_12dof/
2026-08-29_23-56-49/nn/
last_quadruped_current_v3_assembly_four_leg_linkage_12dof_ep_250_rew_28.964792.pth
```

Matched deterministic flat evidence:

```text
/workspace/projects/training/logs/rl_games/
quadruped_current_v3_assembly_four_leg_linkage_12dof/
2026-08-29_23-56-49/evaluation/20260830T000731Z-currentflat/result.json
```

Forward command results: 0.067 m/s for a 0.18 m/s command, 0.234 m signed
displacement, 3--4 landings per foot, 0.052 mean tilt, and 0.058 mean foot
slip. Reverse, lateral, yaw, and posture segments fail because Core did not
train those commands.

Visual evidence:

```text
training/visual_review/current-v3-core-epoch250/rollout.mp4
training/visual_review/current-v3-core-epoch250/contact-sheet.jpg
```

The contact sheet shows a level alternating four-foot gait with no obvious
dragging or collapse. It remains the strongest general locomotion evidence.

## Evidence-backed iterations

1. Ran bounded CurrentV3 Rough experiments at `2026-08-29_21-58-10`,
   `2026-08-29_22-55-16`, and `2026-08-29_23-29-17`. Their retained
   epoch-25/100/125 checkpoints failed matched flat regression through static
   or one-foot-air behavior, so none was promoted.
2. Corrected false chassis-contact fall charging. Before the fix, training
   charged about -4,792 fall reward per episode while termination reported no
   fall. The reward now follows the explicit base-contact termination contract;
   post-fix fall reward is zero.
3. Increased the existing overlong-air safeguard after matched epoch-100/125
   evaluations exposed a stationary one-foot-air solution.
4. Rejected mixed pose-goal, direct signed-command, neutral-posture, and
   posture-at-once continuations after they erased the Core gait.
5. Introduced categorical reverse, strafe, turn, diagonal, posture, and rough
   continuation stages. These preserve familiar command rehearsal and prevent
   uniform command sampling from starving pure axes.
6. Reverse continuation produced incremental signed reverse motion but did not
   pass: +0.009, -0.017, -0.037, and -0.020 m/s across matched iterations.
   Complete-cycle constraints activated every deterministic foot but traded
   speed away; increasing only signed-velocity weight recovered 0.051 m/s
   forward and -0.034 m/s reverse.
7. A clean symmetric restart changed methods after continuation plateaued. Its
   saved-best checkpoint learned four-foot reverse at -0.041 m/s but collapsed
   forward to 0.006 m/s. This is a command-mode collapse, not a promotion.

Representative failed evidence:

```text
# Best transferred bidirectional attempt
/workspace/projects/training/logs/rl_games/
quadruped_current_v3_assembly_four_leg_linkage_12dof/
2026-08-30_02-04-13/evaluation/20260830T021120Z-currentflat/result.json

# Clean symmetric restart
/workspace/projects/training/logs/rl_games/
quadruped_current_v3_assembly_four_leg_linkage_12dof/
2026-08-30_02-15-37/evaluation/20260830T022952Z-currentflat/result.json
```

Clean-restart visual failure evidence:

```text
training/visual_review/current-v3-symmetric-failure/rollout.mp4
training/visual_review/current-v3-symmetric-failure/contact-sheet.jpg
training/visual_review/current-v3-symmetric-failure/forward-reverse-contact-sheet.jpg
```

The dense forward/reverse sheet confirms the numeric diagnosis: the legs cycle
and change pose, but the body makes very little forward progress; reverse has
visible body pitch and asymmetric leg travel. Later command segments settle
into mostly static, planted-foot poses. This is neither hidden sliding nor a
valid controllable gait.

All matched evaluation outputs are immutable beneath their experiment's
`evaluation/` directory. Zero-segment Isaac startup failures were recorded as
infrastructure failures and never interpreted as policy results.

## Promotion and deployment status

- `currentflat`: failed.
- held-out rough `current`: not run for promotion because flat failed.
- broad randomization/current stress `currentstress`: not run for promotion
  because flat failed.
- posture variant: not trained after mobility failed.
- export/ONNX/Android deployment bundle: intentionally not created.
- physical robot run: not performed.

The next technical problem is command-conditioned mode collapse. A credible
next method should explicitly preserve multiple deterministic command modes
(for example, command-balanced distillation or a mixture/router with a shared
279-value contract) and must beat the immutable Core and reverse baselines on
the same 21-segment screen before rough training resumes.
