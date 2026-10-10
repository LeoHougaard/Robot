# One-second operator lease alignment, 2026-10-10

Based on checkpoint `f60b411362bfdc25452c6237ff8bb074a4867429` on
`review/robot-v4`. Operator-only named-axis velocity authority now accepts
1..1000 ms. Boundary regressions verify writes immediately before expiry and
rejection exactly at expiry, plus rejection of oversized replacements without
extending or replacing an existing lease. The shadow provider/preview policy,
Bonsai loopback support, physical eligibility and promotion flags are unchanged.

## Verification

The full canonical offline profile passed all 20 requested gates and two
source/dependency stability checks: 807 tests, plus seven CPU subtest reports,
zero failures, errors or skips. The separate host suite passed 159 tests.
The existing pinned Linux environment used Python 3.12.14, Java 17.0.20.1,
Node 24.19.0, NumPy 2.2.6, ONNX Runtime 1.24.3, CPU Torch 2.5.1 and BAML 0.226.2.

Linux Git cannot use the original Windows worktree's absolute gitdir path.
Testing used a disposable normal checkout. All 615 tracked source files were
copied and byte-hash verified against the modified Windows checkout; the four
reviewed files were verified unchanged again before this handoff. This handoff
was added after testing and contains documentation only.

The first Linux run on the Windows-mounted mirror passed 19 requested gates
but errored in `test_wrapper_child_inheriting_stdout_is_terminated` during its
200 ms synthetic process exchange. Its full result is retained as FAIL.
A source-identical native-filesystem full rerun passed without changed tests,
assertions, deadlines or dependency pins. This supports an infrastructure
startup/filesystem timing explanation; it does not erase the failed attempt.
The earlier Windows full run and its platform/dependency failures are also
retained. No Windows compatibility changes were made.

Android `testDebugUnitTest`, `assembleDebug` and `assembleDebugAndroidTest`
succeeded with existing Gradle 9.5.0, Android Studio JDK 25.0.2 and installed
Android SDK, using `--offline --no-daemon`, disabled SDK/toolchain downloads,
and `-ProbotReviewInstall=true`. Android unit tests: 158 passed, zero failures,
errors or skips. Build: 81 tasks, 10 executed and 71 up-to-date. Existing Kotlin
warnings remain. No license acceptance, downloads, installation or
instrumentation/device execution occurred.

## Evidence retained locally

Task evidence root: `C:\Users\Leo\Documents\Codex\2026-10-10\task`. Raw reports/caches/APKs remain outside Git.

- `lease-mirror-source-proof.json`: all 615 copied file identities.
- `lease-linux-mirror-validation/latest.json`: mounted run FAIL;
  SHA256 `9bb2595c7637f33ef51b76fe2106a6ef1b6f1698a40e2e73ab662a053e90e772`.
- `lease-linux-native-validation/latest.json`: full profile PASS;
  SHA256 `8dbc60fc36b70d98bed7e1944b8597ee173fbfa1f8051a166ad5b7e6695587b3`.
- `lease-android-offline-build.log`: successful offline build;
  SHA256 `d9e3f5dcdcd5deed191b9283f1924f00e2f942a61f33f71963358052a7f883b7`.
- `lease-final-build-summary.json`: counts and exact local APK paths.
- Debug APK SHA256: `69a6c192fcbc5df058f4f19f0a7e674b21e65ef3a370761dd102950dc354f392`.
- Test APK SHA256: `809003e1754998a5d757c3b3e702c0706b78cbf2e9aec56a4cf0d26318879c75`.

No physical safety, firmware receipt, torque-off, provider qualification or
hardware acceptance is inferred. Physical eligibility stays unchanged. Training,
simulation, provider calls and hardware work remain paused; no push occurred.
Protocol/firmware reconciliation awaits actual V5 source.
