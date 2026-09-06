# First speed continuation from physical walking

Leo requested training with Luna supervising, and faster forward, backward,
sideways and turning motion. The physical baseline remains the epoch 2750
bundle tagged `robot-first-floor-walk-20260906`.

This first bounded experiment adds `CurrentBodyV22Speed`. It keeps the eight
existing slow command rows and adds six signed commands: forward/backward at
0.06 m/s, left/right at 0.03 m/s and both yaw directions at 0.15 rad/s. The
initial command and 4 to 6 second command holds are unchanged. This is one
curriculum stage, not an automatic progression to arbitrary speeds.

Continue the exact epoch 2750 checkpoint for 500 additional epochs, targeting
epoch 3250 with 128 environments. Preserve actor/critic weights, normalization
and optimizer state through the existing delivery checkpoint contract. The
CAD drive convention, geometry, stride reference, servo fit, action limits,
rewards, PPO settings and flat terrain remain unchanged. Larger commands
increase horizontal stride displacement through the existing reference math.

This is a speed acquisition experiment. Physics still uses the nominal 20 ms
control step. It does not resolve the real run's 46.4 Hz timing, body lean or
terminal packet fault. Sensor-age/action-delay stress evaluation remains
separate and does not substitute for simulation of actual variable action-hold
durations. No phone or firmware update belongs to this experiment.

## Verification and supervision

- Verify the source checkpoint SHA256 is
  `ed52b4f3502fb3d0fb64ad4b258c3cdc623e37d0fab96dad02e8f11a1644e940`.
- Use the repository launchers and their immutable source snapshots. Record
  the actual run directory, profile, checkpoint, source and process identity.
- Compare the baseline and candidate with the old eight-row command menu and
  the new fourteen-row menu. Evaluator command equality assertions must show
  that the new targets reach the controller. A baseline failure at a new speed
  is an acquisition target; regressions on old commands remain failures.
- Keep `check_stride_results.py` unchanged. Require its signed progress,
  tracking, upright, height, repeated lift/landing and stopped-support checks.
  Screen 20-second runs first, then compare 60-second endurance and physical
  variation for candidates worth retaining. Render complete videos for visual
  review. Include stationary starts in the acceptance evidence.
- Luna checks process identity, epoch progress, fresh logs/checkpoints and
  obvious numerical failures about every five minutes. It alerts the root
  agent to stalled/failed jobs and completed evaluations. Root reviews the
  first useful comparison and the final candidate evidence.
- Training reward does not select a deployed policy. Record the outcome as
  retain for further evaluation, reject, or inconclusive. Preserve epoch 2750
  and all raw evidence regardless of the outcome.

The next curriculum stages must be justified by the measured results. Higher
speeds, combined translation/turning and compressed-height rough terrain are
later targets. This first experiment does not claim those capabilities.

## Started run

The run started as `20260906T170329Z-train-51934`, using source commit
`51bab93` and the launcher's source snapshot. The output experiment is
`2026-09-06_17-03-44` under the existing V22 RL-Games directory. The source
checkpoint hash matched the baseline above. Actual epoch advancement was
verified at 2783 and 3044; Luna subsequently checked epoch 3084.

`run_speed_postppo_supervisor.sh` waits for the exact PPO process, requires
successful training completion and an otherwise idle evaluation backend, then
runs matched baseline/candidate slow and speed screens. It uses the frozen
source, run profile and simulation fit. Evidence goes to
`training/reviews/20260906-speed/20260906T170329Z-train-51934-eval-v3` on the GB10.
The original waiting supervisor had invalid container paths and runtime
arguments; it was stopped before evaluation and corrected without touching
PPO. The replacement's Bash syntax and container-visible inputs were checked.

Monitor training with `Get-SimpleDogTrainingStatus.ps1`; monitor evaluation
through that review directory's `status`, `supervisor.log`, per-case logs and
gate JSON files. A process launch is not evidence that the evaluation passed.

PPO completed all 500 additional epochs in 1508.33 seconds. The periodic epoch
3250 checkpoint SHA256 is
`744024c2f692f8d4778d81c487e759426fd2820cc2214f067a4146aa51937668`.
RL-Games also wrote a differently named final archive with identical actor
tensors, epoch 3250 and frame 26624000. Evaluation pins the periodic archive.

Early evaluation attempts stopped on setup problems: duplicate final checkpoint
names, then the profile loader's approved-directory restriction. Their evidence
is retained separately. The v3 supervisor uses approved profile/fit paths and
requires their bytes to match the frozen run copies. It also requires a complete
result with the expected number of command rows before applying behavior gates.
The first valid baseline rollout completed under v3; candidate decisions and
visual review are pending.
