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

## Robustness and sustained-command investigation

Robust continuation through epoch 2250 and sustained-command continuation
through epoch 3250 are preserved in `cad-duration-robust-1250`,
`cad-duration-robust-2250` and `cad-duration-sustained-3250`. The latter uses
70-second episodes and command holds from 4 to 60 seconds. Epoch 2750,
SHA `ed52b4f3502fb3d0fb64ad4b258c3cdc623e37d0fab96dad02e8f11a1644e940`,
passes nominal and varied 20-second commands, including seeds 45 and 46.
The initial batch endurance failures repeatedly involved stopped environment 6
and forward-moving environment 7. Separate single-robot endurance videos pass.

The subsequent paired collision diagnostic changes only scene spacing, keeping
eight environments, seed 42, the same actor and commands, and 60 seconds:

- At 1.5 m spacing, environments 6 and 7 approach within about 0.30 m. The
  stopped robot loses foot support and the moving robot leans and slows.
- At 8 m spacing, all exact intermediate checks pass. Late forward speed is
  about 0.045 m/s and tilt is 0.024 to 0.027 rad.

The installed Isaac Lab cloning path requires explicit collision filtering;
the project's scene setup only calls that filter for CPU simulation. These
results support interference between neighboring simulated robots, rather than
an isolated-policy endurance failure. The next verification fixes GPU collision
isolation and repeats the original 1.5 m tests, retaining ground contact and
the original gates. The wide-spacing diagnostic alone does not promote a policy.
Long training runs may also have encountered neighboring robots; every retained
candidate needs evaluation in the corrected scene before selection.

Evidence: `collision-diagnostic/collision-diagnostic-report.json`, SHA
`08cd482bd9d0767c96dee60921ac2a01429e7345a98af0dea6baafe4e878415d`.
Earlier endurance reports remain available as evidence from the interfering
scene. Their claims about policy failure are superseded by this diagnosis.
The exact gate is now committed in `check_stride_results.py`; its function AST
matches the original intermediate screen. Earlier agent summaries incorrectly
used peak tilt or an eight-landing minimum. Corrected reports use mean tilt,
six landings per 14 measured seconds, and all original contact/command checks.

Epoch 750 separately passed Torch/portable and ONNX Runtime comparisons on
531 observations, plus a 3,000-frame fixed sensor replay using its own actor
actions through production `PolicyFrameSession`. Evidence and reproduction
scripts are in `training/reviews/20260906-delivery/cad-actor-parity-v22-epoch750`.
This verifies software math, not physical sensor dynamics or powered USB timing.

## Retained flat test candidate after collision isolation

Epoch 2750 is the retained candidate for the supervised flat-floor test.
The GPU collision fix is `f8dd552`. At the original 1.5 m spacing, the exact
checker passes nominal 20/60-second commands, variation seeds 42 through 46,
variation 60 seconds, and timing 20/60 seconds. Historical epochs 1250 and 2250
also pass their corrected nominal/variation endurance checks. Root independently
ran the committed checker on all 16 copied fixed-scene result files, with no
failures, and inspected walking/stop frames spanning 2 through 58 seconds.

Original SDF nominal20/60 checks also pass. The derived profile differs only in
the verified source asset from the conversion manifest. At 60 seconds, forward
speed is 0.04320 m/s with SDF versus 0.04529 m/s with convex geometry; mean tilt
is 0.02791 versus 0.02412 rad. The source-controlled evidence-only guard validates
the conversion, asset/layer hashes and profile identity. The training contract
remains bound to its original profile. Evidence is under `collision-fixed-source`:
`collision-fix-report.json` and `sdf-fidelity-final-report.json`.

The exact 2750 actor passes 531 Torch/portable/ONNX comparisons and a 3,000-frame
fixed sensor replay through production `PolicyFrameSession`, including restarts.
Reproduction files are in `training/reviews/20260906-delivery/cad-actor-parity-v22-epoch2750`.
Its training source is `6a126d6`, run `2026-09-06_10-33-19`; the earlier `02b9109`
is ancestry, not the 2750 training revision.

The candidate APK pair and portable bundle are in
`Videos/Robot-policy-review-20260905/candidate-epoch2750` on both Windows machines.
The instrumentation APK includes the candidate and a motor-disabled transport
test. Production live stride loading remains guarded. Full host unit tests and
APK builds pass; device instrumentation has not run. Firmware 0.1.15 is built,
not flashed. Follow `pixel_robot/docs/DELIVERY-TEST.md` when the board and Pixel
are connected. Physical calibration, motor-disabled USB timing and Leo's observed
powered test remain required. Rough terrain, mixed commands and broader speeds
are later stages; this record establishes the slow flat simulation test envelope.

The Pixel 10 now has the candidate diagnostic APK installed with the existing
signing key, preserving app data. The offline device test passes with exact
checkpoint reference actions, `PolicyContract`, CAD calibration and
`PolicyFrameSession` validation. Device testing caught three packaging errors:
history was labeled 6 instead of 24, calibration used the legacy linkage
convention, and six reference answers were not the exact actor's outputs.
Commits `43bb4f8`, `4a2362f` and `e1267b2` correct those artifacts. The trained
weights remain unchanged and byte-equal to the checkpoint tensors. Command
limits now match the tested isolated axes: 0.04/0.02 m/s and 0.1 rad/s.

Verified installed test APK SHA is
`fcf94140473f4bf2f7424dfb6bc7ebd25c82c33b18b9483c06939978545702e6`.
The canonical packages on both Windows machines contain the same APK and its
exact embedded assets. `desktop-pixel-evidence/candidate-smoke-verified.txt`
records `OK (1 test)`. Wireless ADB was reconnected using mDNS discovery. The
ESP32 is not yet connected to the desktop; flashing, transport timing and
physical motion remain pending.
