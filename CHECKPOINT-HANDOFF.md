# Robot V4 checkpoint, 2026-10-10

This checkpoint saves the existing Robot V4 integration on `review/robot-v4`.
Its parent is `27878e11f8d1c023fe9e1c1f62b4a3e18ba1f869`. It includes all 132
reviewed source, test, configuration, fixture and documentation paths that were
dirty before checkpoint metadata was added. The initial index contained 121
paths, with 21 unstaged tracked changes and seven untracked integration files.
Some paths appeared in both staged and unstaged sets.

The saved work covers strict LLM intent contracts and qualification, shadow
request expiry and cancellation, immutable policy assets, bounded USB writes,
independent operator leases, offline transport regressions, deployment preflight,
and timing/reference diagnostics. The later integration fixes and device test
source are included. No policy bundle was replaced or promoted.

## Review and preservation

The checkpoint review inventoried both index and worktree diffs, inspected the
untracked integration files, screened all changed text for private key material,
recognizable live tokens and quoted secret assignments, and reviewed absolute
private paths as operational context. The screens found no credential matches.
This was a preservation review, not a new functional audit or hardware acceptance.
No implementation changes were made during checkpointing.

The original staged tree is `a314e14f06b38d66daa3c5055cbc0c18e6296529`.
The initial staged and unstaged patches remain locally at
`.tooling/checkpoint-initial-index.patch` and
`.tooling/checkpoint-initial-worktree.patch`. A local reviewed-file manifest
records the original 132 worktree file hashes. Their hashes and the original
counts are committed in
[preservation-summary.json](artifacts/checkpoints/robot-v4-20261010/preservation-summary.json).
No reset, discard, history rewrite or push was used.

## Existing validation

No tests were rerun for this checkpoint. The saved final offline report from
2026-10-02 passed all requested offline gates. All 612 files in its source-after
manifest still matched the checkout byte for byte before checkpoint metadata was
added. That comparison establishes source identity with the saved evidence.
It does not rerun the checks or include the newly added checkpoint metadata.

The retained report records 157 Kotlin host tests, 48 shadow-panel DOM tests,
20 CPU actor/runtime checks and 68 CPU Torch tests, plus the Python contract,
transport, preflight and runner suites. The Kotlin host runner excludes Android
service, activity and USB transport classes from execution. The UI uses DOM/fetch
stubs. These limits remain relevant even though separate device evidence exists.

The saved device instrumentation output reports six passing selected
service/lifecycle/frame/parity tests. The existing integration status records
debug/test APK builds, a disconnected phone and no robot transport. Separate
firmware-host and compile-only records exist. They do not establish board timing,
firmware receipt, watchdog behavior or torque-off on a physical robot.

Three saved Bonsai responses produced strict intents but took about 70.4, 61.9
and 32.5 seconds. None met the app's eight-second deadline. The provider remains
unqualified. Browser DOM checks were recorded; browser visual acceptance remains
unverified because screenshots failed. The native phone screen was inspected
during the earlier integration session, not during this checkpoint.

[evidence-summary.json](artifacts/checkpoints/robot-v4-20261010/evidence-summary.json)
contains curated results, source identities and hashes of the original local
reports. Full reports and raw evidence remain excluded. The outstanding gates
inside each report describe that runner's scope; separate device or firmware
evidence does not turn the offline runner into hardware acceptance.

The existing index whitespace check reports one blank line at EOF in
`robot_dog/firmware/robot-dog-control/test/platformio.compile-only.ini`.
It was preserved to keep the recorded source bytes unchanged.

## Exact exclusions and reproduction limits

These directory prefixes are excluded in full and retained locally:

| Relative path | Contents |
|---|---|
| `.tooling/` | Host-specific integration helpers, source archive, screenshot and checkpoint preservation records |
| `pixel_robot/.gradle/` | Gradle cache and locks |
| `pixel_robot/.tooling/` | Downloaded Kotlin dependencies and compiled host classes |
| `pixel_robot/app/build/` | Android build intermediates, APKs, reports and generated output |
| `robot_llm/__pycache__/` | Python bytecode |
| `validation/results/` | Complete offline attempts, device captures, raw provider requests/completions and firmware build logs |

The exact local file paths, byte sizes and SHA-256 hashes are in
`.tooling/checkpoint-preservation-20261010.json`. Its committed summary records
2,483 preserved files totaling 475,178,878 bytes. The manifest itself is excluded
from its own file list; it also remains locally and its hash is committed.
No excluded file was deleted or moved. Authored small corpus and fixture files
under `robot_llm/data/` are source and are included.

A fresh clone has the source, authored fixtures, existing tracked runtime assets,
dependency pins and curated evidence. It does not have the excluded caches,
APK binaries, raw provider outputs, screenshots, full test logs or local helper
scripts. Historical evidence cannot be reconstructed from its hash alone.
Use the source workflows in `validation/README.md`, `pixel_robot/docs/HOST-TESTS.md`
and the firmware test README to generate new evidence in an authorized session.
The original recorded physical-capture gate is still unavailable. Isaac
acceptance, model qualification and physical acceptance remain outstanding.

Training, simulation, services, installation, flashing and motion remained
paused during checkpointing. Resume only within Leo's separately authorized
scope. Physical controls must remain idle until an approved operator session.
