# V5 source and physical handoff, 2026-11-12

Continue source work in
`C:\Users\Leo\Documents\Codex\2026-10-10\task\robot-v5-reconciled`,
branch `review/robot-v5-reconciled`, reviewed starting revision
`9729499bbfb44ea98dfc2af16c96c60aca0c6ee0`. This document's changes are the
next commit on that branch. This is the current development source for this
handoff, not a claim that it has replaced the registered project or installed app.

## Recovery and retained work

| Copy inspected read-only | Revision | Role |
|---|---|---|
| `C:\Users\Leo\Code Projects\Robot-delivery` | `27878e11f8d1c023fe9e1c1f62b4a3e18ba1f869` | Clean older delivery reference |
| `C:\Users\Leo\Code Projects\Robot` | `97e7acef786f2d007479ab7d281efd7974a1eced` | Registered old root; has local work, preserve it |
| This checkout | `9729499` before this handoff | Clean reconciled V5 plus independent review fixes |

The registered root has modified `.gitignore`, untracked `robot_agent/`,
`artifacts/reviews/`, `training/SIM-TO-REAL-REVIEW-20260904.md` and
`training/fits/current-body-v5-8ec33a2eeac8.json`. None was copied, removed or
committed here. Do not reset that copy or redirect it through this handoff.
`CHECKPOINT-HANDOFF.md`, `V5-RECONCILIATION.md` and
`REVIEW-FIXES-20261010.md` explain preservation and dated validation. Full
October evidence remains outside Git under the parent task directory. Hashes
alone cannot restore excluded evidence. Remote checkpoints remain authoritative
on the GB10; no remote access is needed for this work. Learned navigation stays paused.

## Exact candidate identities

These identify source assets, not the currently installed system. Verify both
APK hashes and the effective stored calibration in an operator session; version
strings alone do not identify binaries. Existing app data must be preserved.

| Component | Source identity |
|---|---|
| App | `com.leo.pixelrobot`, version `0.2.11-ai-test`, code 13; test package `com.leo.pixelrobot.test` |
| Optional review build | `robotReviewInstall=true` adds `.review`; do not use production-package capture commands for that variant |
| Firmware | `0.1.18-transportguard`, nonce-v2 abort capabilities, Pixel UART 2,000,000 baud |
| Profile | `assembly-four-leg-linkage-12dof`, SHA256 `2e249f1a8efc7df0fca72dfaf2e50712a2b108874bdf7a883cbd69f02b25ca0d` |
| Policy | V22 epoch3750, 428 observations, 12 outputs, `cad_drives_v1`, 50 Hz |
| Checkpoint SHA256 | `964b3d4fc7e40ce841e1ef6167f162d0af89d8f9f8057757bf07e16ba79ea8b5` |
| Export weights SHA256 | `4a4b716411962ee95c1cd6e6fafd815d8d9e8020eaa2393ef1edc4c3f1491c2e` |
| ONNX SHA256 | `b12395bccd9410a7c1ac4017e46b70e9b161cc3641839b9ada2d08252c20142a` |
| Stride reference SHA256 | `b2db6928c7aa3d7acb521a5f254250807a371f2eaac75783ff087b0240217fc2` |
| Packaged calibration byte SHA256 | `6d5cccade24a54c8ff98e7e194f41af676c9f6268be15e44d9293b649090bfa6` |

The source of these identities is `pixel_robot/app/src/main/assets/` and
`pixel_robot/app/build.gradle.kts`. Both promotion and physical transport flags
remain false. The 180-input `assembly-1-12dof` Python runtime and its historical
921600-baud commissioning are separate; its tests do not qualify V22.
Firmware local binary generation evidence is in
`robot_dog/firmware/robot-dog-control/test/local-v5-compile-evidence.json`.
It records a different cached toolchain from the blocked registry-pinned build,
and a post-build update error. No binary was flashed here.

## Finite motors-off operator session

Use the existing procedure in
[HARDWARE-ACCEPTANCE.md](deployment_readiness/HARDWARE-ACCEPTANCE.md), specifically
`Installed3750MonitorTransportTest`, after separately approved paired installation.
This handoff does not authorize installation or execution. Leo confirms the
supported dog12 body, IDs, effective calibration, servo rail and disabled torque.
The monitor requires register readback; disarm alone is insufficient.

1. Record exact app/test APK and firmware binary hashes, displayed versions,
   source revision, body/profile and effective calibration. Unknown installed
   hashes remain an open identity gate. Use the exact current device serial.
2. Run one existing 1,250-frame diagnostic (30-second monitor lease, roughly
   25 seconds of frames). Retain its sidecar and exact raw capture filename;
   run the offline analyzer as documented. Require nonce ACK before LF,
   complete feedback, no UART error deltas or missed periods, and unchanged
   torque-off register observations.
3. Repeat once after board power-cycle and once after USB unplug/replug. Each
   attempt gets its own intact session. Confirm no automatic arm or target
   writes. Compare identity, startup, feedback and timing across the three runs.
4. In one separately recorded monitor-only fault attempt, have the operator stop
   the app or disconnect the link. Preserve partial captures and observed
   teardown outcomes. An interrupted capture is diagnostic evidence, never a
   passing acceptance input. Reconnect must require a fresh handshake; record
   physical torque readback independently after reconnection.

Then stop and review. The existing timing assertions are host/firmware rate
49.5..50.5 Hz, sample p99 <=25 ms/max <=40 ms, inference/send p99 <10 ms,
frame p99 <20 ms, complete currents >=99%, zero missed periods and UART errors.
The analyzer has further fixed completeness/framing checks; none was loosened.
All of those physical measurements remain outstanding for this exact pair.
Driver queue delay and absolute sensor age remain unmeasured without synchronized
clocks. Monitor teardown does not establish a live torque watchdog response.
Loaded actuator delay, physical stop latency, watchdog torque-off under load,
mechanical limits and walking stability require later explicit motion approval.

## Action diagnostics and software evidence

`pixel_robot/tools/fit_sim_from_run_data.py` now reports consecutive requested,
filtered and applied vector changes, maximum joint steps and host-time rates,
alongside existing saturation fractions and exact outside-[-1,1] frequency.
Missing vectors, sequence gaps/reordering or dimension changes do not create
interpolated pairs. Missing timing produces null rates. For V22, requested action
is the actor residual, not the combined reference-plus-residual target. Existing
logs cannot recover that pre-clamp target or measured actuator smoothness.
`training/probe_delivery_gait.py` already reports raw reference clipping and
residual headroom, without running Isaac.

Historical `mean_action_rate` and `max_action_step` gates remain unchanged.
Applied actions bounded to 0.30 per joint imply a squared vector change <=1.08
for 12 joints, already below the 2.50 gate, and 0.30 is below the 0.42 step gate.
Such a pass cannot establish actor smoothness. Some legacy evaluation paths use
filtered-action differences instead; preserve the signal's actual provenance.
Do not reinterpret old passes or invent raw combined targets from clipped data.

The portable actor now rejects non-finite outputs before clipping. Previously
positive/negative infinity became finite +/-1 commands. A simulated complete
trial now rejects that output before any target and verifies fake shutdown.
Finite actor outputs, weights, thresholds and control behavior are unchanged.

Fresh local checks used Python 3.14 / NumPy 2.5.3, not the full pinned validator:
88 portable runtime tests passed, including framing, stale feedback, inference
stall, cancellation, reconnect and fake torque readback. Capture-fit tests passed
13 with one missing historical-data skip; the complete capture-analysis workflow
test passed. The 68-test hardware analyzer attempt had 66 passes, one platform
skip and one Windows symlink-privilege error. It is not a complete suite pass.
No Android/firmware build, device run, provider, training or Isaac job ran.

Reproduce the bounded software checks from this root:

```powershell
python -B -m unittest discover -s robot_dog/policy_runtime -p 'test_*.py'
python -B -m unittest discover -s pixel_robot/tools -p test_fit_sim_from_run_data.py
python -B -m unittest discover -s pixel_robot/tools -p test_analyze_training_capture.py
python -B -m unittest deployment_readiness.test_hardware_acceptance
```

The new actor tests were also run with only `NumpyPolicy.action` extracted from
`9729499` in memory. That negative control reproduced three expected failures
(+infinity, -infinity, and a simulated trial sending targets), zero errors.
The corrected source passes those same tests. The packaged ONNX bytes were
checked against `policy_android_manifest.json` and match. No weights changed.
