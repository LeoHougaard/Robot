# CAD drive coordinates

The detailed CAD is authoritative, as Leo confirmed in this conversation.
The older serial-leg model is an approximation of the same mechanism. Its
relative knee coordinate requires a transmission conversion. That conversion
must not be applied again to a model whose selected drive is already the
hub-to-servo-link angle.

## Evidence and consequence

The old `assembly-1-12dof` front-right knee joins femur `_Mqv1zKhsEzD3iceNj`
to tibia `_MbBQZAdjJlGER7cV8`. In the current asset, the selected knee drive
`_MDg4yzrzmMKgaKG7L` joins hub `_MBK47NygQU_fnvKKJ` to servo link
`_MTn8HoecFwNHGSxBF`. Its relative femur-to-tibia joint is passive. All four
legs have this relationship. The original Onshape export marked the three
hub drives as driven; repair commit `856256a` retained that selection and
closed the passive loops.

The calibration added for the new asset in `b97bd18` nevertheless retained
the old `four_bar_follow` entries. V20/V21 also wrapped the measured servo
trajectory and encoder model in that conversion, while reading and writing
the selected CAD drives directly. Agreement between those software formulas
did not establish agreement with the mechanism.

For example, a normalized semantic hip angle of 0.1 rad and knee drive angle
of zero asks the CAD knee actuator for zero. The inherited phone conversion
asks the physical knee servo for another 0.1 rad, about 5.73 degrees. The
closed linkage already changes the foot pose when the hip moves.

V22 therefore uses `cad_drives_v1` for the twelve signed, rest-relative CAD
drive angles. Encoder zeros, servo IDs, sign calibration and finite limits
remain explicit. There is no added hip term in knee targets or feedback.
The detailed passive linkage resolves its own geometry and load response.
The measured raw-servo speed/acceleration fit remains applicable in motor
coordinates; this correction does not identify real loaded joint dynamics.

## Isolation and verification

V21 remains reproducible as a rejected historical experiment. V22 starts
from a zero residual actor and fresh optimizer state. It cannot resume a
V21 actor based on matching dimensions. Its checkpoints bind coordinate
convention, reference, profile, asset and servo-fit hashes. Launchers and
actual restore/evaluation methods reject cross-family input.

The reference retains the measured inverse Jacobians, which were taken
against the direct CAD drive coordinates. A new reference file explicitly
declares those coordinates. Its frequency, duty, lift and residual bounds
are unchanged for the first comparison. It is still a local linear foot
approximation, not a complete inverse solution of the linkage.

V22 acquisition uses the already tested command-stage heading variance of
0.01 from its first update, and the same stopped-support cost of 0.25 per
unsupported foot per second. V21's weaker heading loss admitted a learned
curve despite correct forward speed. The zero-actor physical comparison is
unchanged by this reward-only setting; training reward is not a promotion gate.

Evidence directory:
`reviews/20260905-delivery/cad-coordinates-preflight-v1` on the GB10.

- Seventeen CPU tests pass, including independent motor commands, bounded
  motor trajectories, critic continuation, contract rejection and reward cases.
- The actual USD drive graph and bound file hashes pass inspection.
- Forty-nine fixed-body poses pass. Maximum drive target error is
  0.000923902 rad. Maximum encoder reconstruction error is 0.000741065 rad,
  less than half a 4095-count encoder step. Root displacement is below 5 nm.
- Matched V21 retention reproduces every result and time window exactly.
  The corrected zero actor travels at 0.03879 m/s over the minute-long test,
  with mean tilt 0.04254 rad and no falls. Absolute lateral motion remains
  0.02944 m/s. The command screen keeps every foot cycling in motion and all
  feet down at stop; negative strafe is weak at -0.0117 of -0.02 m/s.
- The reference video was inspected at overview and stride timescales. It
  shows sustained stepping and travel. Video SHA is
  `7159705b5484ddeb036b983f978e26e188a05f325536adcc64011cb1d3970c5d`.
  A final supervisor comparison used an incorrect baseline directory after
  all cases finished. Its failed status is preserved; `root-review.json`
  records the independent completed comparison using the correct source.
- Phone parity uses a separate direct-drive test calibration. Existing live
  bundles still use their original convention. V22 live loading is rejected
  until the controller, export and accepted bundle are complete.

Twenty-three affected Android tests and Kotlin compilation pass. The new
session's 3,000 synthetic frames run twice across a full reset. An initially
missing V22 observation dispatch failed at the dimension check and was fixed.
The exact reference and fixture were then normalized to Git's LF format;
fixture regeneration is byte-exact, and the four session tests pass again.
These tests do not establish physical USB timing or loaded servo response.

Training uses the exact committed LF bytes for the servo-fit and reference
files. Preflight-v1 used equivalent JSON with working-copy line endings.
Its original snapshots and checkpoints retain their original byte hashes;
the final initialization must bind the committed files before PPO.

## Remaining plan

1. Completed: fixed poses, matched historical retention and corrected stride.
2. Completed: bounded flat acquisition and matched reference comparison.
3. Completed: nominal isolated forward/lateral/yaw command checks. Next:
   model variation and measured latency/servo uncertainty. Keep posture neutral.
4. Advance actual terrain heights only after acceptance, retaining flat cases.
   Use the reviewed compressed-height curriculum, not elapsed training time.
5. Verify portable actor and session parity, motor-disabled 50 Hz USB timing,
   and the user's physical calibration/low-speed test before deployment.

The CAD audit cannot certify encoder signs and offsets on the assembled
robot. The first physical check must compare an isolated small hip/servo-link
pose with the same CAD pose while Leo observes. No agent-driven motor motion
or unverified policy promotion is part of these checks.

## Acquisition run and phone session

The first V22 run is `20260906T034954Z-train-12533`, with source `41677bd`,
128 environments, seed 42 and a total target of 500 epochs. Its initialization
SHA is `e873845ebbcb4314b4ab58b55439c6ede524b4dd5918c581bef6a3d55b788f68`.
`cad-final-source-preflight-v2` verifies the exact deployed Git bytes and
reproduces every physical result and window of the reference comparison.
`cad-500-followup` waits for normal completion, then checks zero/250/500 flat
motion, zero/500 endurance, stationary-start commands and the epoch-500 movie.
It cannot promote a policy or start more training. Luna monitors read-only
at about five-minute intervals.

The phone controller now uses `PolicyFrameSession` for observation history,
stride clock, reference/residual composition, action filtering and initial
hold. The same class runs the Torch-derived sensor replay tests. It accepts
one observation and action before fresh feedback completes the frame. A new
run resets all history and clock state. Derived frame records include the
stride time used for that inference. Legacy actors retain their action path.

Kotlin compilation and the affected session, sensor, action-math and operation
tests pass. V21 and V22 each reproduce two complete 3,000-frame sessions with
the original tolerances. Live stride asset loading stays disabled pending an
accepted actor bundle and transport verification. The installed APK is unchanged.

Evidence is in `cad-controller-session-v1`. The V22 epoch-250 actor separately
matches portable NumPy inference in 531 cases, including 31 sensor replay
observations, with maximum absolute error 0.000000477. The checker rejects
a legacy-coordinate fixture even though its observation size also equals 428.
`cad-actor-parity-v2` retains the report and the initial missing-dependency
attempt before the standalone check received the existing portable math file.
This is numerical parity, not a walking acceptance result or ONNX export.

## Retained acquisition and rejected command continuation

Acquisition epoch 250 is the retained baseline. Its minute-long flat check
has forward speed 0.04182 m/s, absolute lateral speed 0.01510 m/s, mean tilt
0.02062 rad and no resets. Flat seeds 43 and 44 also pass the intermediate
screen. Every foot cycles in the inspected video. Acquisition epoch 500
has similar speed but substantially higher tilt, about 0.055 rad.
Evidence: `cad-250-followup-v1` and `cad-500-followup-v2`.

The command continuation `20260906T045448Z-train-15985` resumed epoch 250
and completed epoch 750 with checkpoint SHA
`cc31cce64ef3b3e25e64956694f81dbf46fdc21ef19ad1f5a2840582caee8be4`.
It improves lateral and yaw tracking but is rejected for increased tilt
and missing foot cycles. The eight-environment command screen leaves FR
planted during positive strafe, and FL/BR scarcely lifting during negative
yaw. Stop keeps all four feet down. The separate one-environment turn movie
also shows the planted BR failure. Individual movie and batch rows need not
match because current-model assignment depends on the environment layout.

The same continuation's intermediate epoch 500 also regresses flat tilt,
to about 0.061 rad. Its command evaluation hit a startup timeout and is
inconclusive; the flat failure is sufficient to reject it as the fallback.
Evidence: `cad-750-followup-v2` and `cad-750-command-review-v1`.

The [moving-foot reward experiment](CAD-REWARD-20260906.md) addresses an
active-reward omission. V22 resolves to V20's reward override; the older
V2/V4 level, complete-cycle and prolonged-air terms are not used. The first
`.25` cost passed physical replay parity but did not reverse the preference
for the measured poor negative turn. Source `02b9109` tests `.4`, with valid
contact cycles and exact legacy source binding. Final replay and a matched
retraining results are recorded below. No V22 policy has been deployed.

## Retained nominal command policy

Run `20260906T062323Z-train-21373` resumed acquisition epoch 250 and completed
epoch 750 with source `02b91094a9d7f76f8296cf751e722788aa36ad99`. Its checkpoint
SHA is `89c9ca8a04b2132f457102003d3a3336f95967f52315b63d963c9dca5798247e`.
The only behavioral experiment was the moving-foot duration cost at `.4`.
The preceding `cad-duration-preflight-v2` reproduced physical rows and time
windows exactly, with only the intended Commands reward difference.

Epoch 750 is retained as the best nominal command-stage baseline. All existing
intermediate checks pass for flat 20-second, flat 60-second and stationary-start
isolated commands. Flat and command repeats at seeds 43 and 44 also pass.
Its intermediate epoch 500 passes flat but fails reverse lateral motion and
negative-strafe progress. Acquisition 250 and both rejected earlier runs remain
available. This decision does not promote a physical deployment bundle.

The minute-long flat result averages 0.04548 m/s forward for a 0.04 m/s command,
0.01144 m/s absolute lateral speed and 0.02281 rad mean tilt, with no resets.
The seed-42 command results are:

| Command | Measured commanded-axis rate | Mean tilt rad |
|---|---:|---:|
| Forward 0.04 m/s | 0.04541 m/s | 0.02293 |
| Reverse -0.04 m/s | -0.04404 m/s | 0.03393 |
| Lateral 0.02 m/s | 0.01647 m/s | 0.02179 |
| Lateral -0.02 m/s | -0.01525 m/s | 0.02352 |
| Yaw 0.1 rad/s | 0.08545 rad/s | 0.02361 |
| Yaw -0.1 rad/s | -0.09986 rad/s | 0.01966 |

All moving command rows have at least eight landings per foot over 14 measured
seconds. Stop has zero airborne fraction for every foot. Root inspected dense
forward and negative-turn frames and the stop overview; they show repeated
stepping, an upright body and a stationary stop. Contact telemetry supports
the correction of the prior planted-foot failure. The gait remains asymmetric;
passing these limits does not establish ideal gait quality or low foot slip.

Evidence: `training/reviews/20260905-delivery/cad-duration-750-training` on GB10,
including `evaluation-report.json`, checkpoint validation and checkpoint-bound
video review. The four `cad-duration-750*.mp4` files and their result JSONs are
SHA-verified in `Videos/Robot-policy-review-20260905` on both Windows machines.

Next checks are physical-parameter, sensor and timing variation on flat ground,
exact candidate actor/runtime parity, then deployment preparation. Combined
commands, broader speeds, rough terrain and physical walking remain unverified.
The motor-disabled transport and observed calibration checks still require the
ESP32 and Leo. Live stride loading remains disabled pending accepted evidence.
