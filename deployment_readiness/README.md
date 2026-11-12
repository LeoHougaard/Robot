# Robot setup preflight and operator handoff

This is the first command to run in a reviewed Robot checkout:

```sh
python -B -m deployment_readiness
```

It reads local files and installed-package metadata and reports the next missing
piece. It does **not** install anything, import the robot runtime/NumPy/ONNX,
invoke Git/Java/Node/Gradle/ADB/PlatformIO, run the validation suite, connect to GB10,
start inference, open a serial port, deploy, flash, arm, or move anything.
Use `-B` to suppress Python's own bytecode cache writes from interpreter startup.
The checker itself has no file-output option; it prints only to stdout.

For machine-readable results:

```sh
python -B -m deployment_readiness --format json
```

Optional arguments:

- `--root PATH`: inspect a different repository without executing its code
- `--android-sdk PATH`: inspect the selected SDK's Android 37 platform jar;
  otherwise `ANDROID_HOME` / `ANDROID_SDK_ROOT` are considered. The checker never
  reads Android credentials, enumerates devices, or starts ADB
- `--node PATH`: locate an existing Node executable, matching validation's option;
  it is never launched. The required version comes from the canonical runner
- `--jvm-cache PATH`: inspect an existing checksum-pinned Kotlin dependency cache,
  matching the canonical runner's option; default `pixel_robot/.tooling/host-kotlin`
- `--validation-report DIR`: read an existing canonical result directory
  containing `latest.json`, `loop.json`, and its named `cycle-NNN/` files
- `--scope offline-setup`: make the exit code reflect only Python, dependency,
  Git/Bash/Java/Node (when the selected canonical plan requires it), Kotlin
  dependency hashes and canonical-runner presence. All remaining checks are still shown.
  This is useful for setup automation; **it never means tests passed**

Exit codes are `0 PASS` for the explicitly requested inventory scope, `1 FAIL`
for a malformed/inconsistent checked contract, and `2 BLOCKED` for missing
requirements/evidence or changed reviewed source. The default `all` scope cannot
return green deployment readiness: Android execution, firmware/transport, model,
Isaac and hardware remain blocked. There is deliberately no override to mark
those stages approved. `deployment_ready`, `physical_execution`, and
`physical_safety_certified` are always false.

## Choose the intended stack before setup

The current source identifies **Pixel as the current four-leg-linkage path**.
The checked-in bundles are distinct; copying one bundle into the other runtime
or adjusting a baud rate to make them appear compatible is not a setup fix.

| Item read from source/assets | Pixel path | Legacy Python/Pi path |
|---|---|---|
| Profile | `assembly-four-leg-linkage-12dof` | `assembly-1-12dof` |
| Observation contract | `current_body_v22_428`, 428 inputs, 24-frame history with six selected 70-value frames | 180 inputs, four 45-value frames |
| Joint convention | `cad_drives_v1`; no relative-knee four-bar addition | relative knee with required `four_bar_follow` |
| Actor | checked-in ONNX, epoch 3750 | checked-in NumPy NPZ |
| File-declared limits | forward ±0.04 m/s, lateral ±0.02 m/s, yaw ±0.1 rad/s | forward 0–0.18 m/s, lateral 0, yaw ±0.25 rad/s |
| Host USB baud | 2,000,000 | 921,600 |
| Promotion claims | `promoted:false`, `physical_transport_verified:false` | historical declared Goal result |

These are file contents, **not measured current robot capabilities or permission
to move**. Current firmware source starts its host USB serial link at 2,000,000.
The legacy Python path therefore also needs a separately matched firmware stack;
its portable math tests remain useful without hardware.

At the current source candidate, app source says `0.2.11-ai-test`, Android
SDK 37, Java source target 17, ONNX Runtime Android 1.26.0; PlatformIO declares
firmware `0.1.18-transportguard`. These are uninstalled candidate versions, not
evidence of a completed app/firmware build or physical test. Consult the preflight's
source inventory after changes. The new app requires the paired nonce-bound
parser-abort handshake before sending a JSON-line delimiter; older firmware is
deliberately rejected. See [FIRST-ROBOT-TEST.md](../pixel_robot/docs/FIRST-ROBOT-TEST.md).
A Java compile target alone does not establish that the installed JDK/Gradle/AGP
combination works. PlatformIO's `espressif32` platform is unpinned, so a source
label alone cannot reproduce an exact firmware binary.

Current `control_center/profiles/*.json` bytes do not equal the historical
profile hashes declared by either portable bundle. The checker warns and leaves
original-export provenance unverified; it does not overwrite hashes. This does
not invalidate unrelated historical math tests. Preserve original actor bytes,
metadata, calibration and profile snapshots when gathering deployment evidence.

## Minimal operator/Codex sequence

### 1. Read-only diagnosis

From the repository root:

```sh
python -B -m deployment_readiness --format json
```

A normal initial result is BLOCKED. Read `offline_setup_status` and each check's
`next_action`; do not interpret a nonzero exit as a request to touch hardware.
Missing tools are diagnosed by PATH or file presence, never by running them.
A package version match is not proof its native library can import or execute.

### 2. Explicit operator setup, once

The following commands create an environment and install packages. They are
**not performed by preflight**, and require separately authorized package
installation/network access. Use the current requirements and setup instructions
in [validation/README.md](../validation/README.md), including any separate runtime
interpreter or host-Kotlin dependencies required by the chosen profile. The
canonical runner is the authority for required gates and dependency pins.

Current offline dependency setup (Linux/WSL), with Java 17+ already installed.
The Kotlin setup command explicitly downloads checksum-pinned dependencies from
Maven Central and writes its local cache; it does not run hardware or tests:

```sh
python3.12 -m venv validation/.venv
validation/.venv/bin/python -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
validation/.venv/bin/python -m pip install -r validation/requirements.txt
validation/.venv/bin/python pixel_robot/tools/run_host_kotlin_tests.py --download-only
validation/.venv/bin/python -B -m deployment_readiness --scope offline-setup
```

Windows PowerShell equivalents:

```powershell
py -3.12 -m venv validation/.venv
validation/.venv/Scripts/python.exe -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
validation/.venv/Scripts/python.exe -m pip install -r validation/requirements.txt
validation/.venv/Scripts/python.exe pixel_robot/tools/run_host_kotlin_tests.py --download-only
validation/.venv/Scripts/python.exe -B -m deployment_readiness --scope offline-setup
```

Keep the venv entrypoint; do not replace its path with its resolved system-Python
symlink. Git, Bash and Java 17+ must already be on PATH. The V4 browser-JavaScript gate
also requires Node 24.19.0, as pinned by `NODE_VERSION` in the canonical runner.
Preflight reports its path and required version without running it; validation
verifies the actual version and executable identity. If using a non-PATH Node,
pass `--node /path/to/node` to preflight and validation. There is no npm-install
step for the browser harness. The exact direct Python pins
are read from `validation/requirements.txt`, its local includes and literal
`Step` declarations in `validation/runner.py`; no runner code is imported. Current
CPU runtime pins are NumPy 2.2.6 and ONNX Runtime 1.24.3. A missing compatible pinned wheel remains
BLOCKED; do not silently change pins. This is an offline test-setup inventory,
not Android SDK installation, firmware setup, or a full validation run.
The Kotlin manifest now includes checksum-pinned OkHttp 4.12.0 and Okio JVM 3.6.0
for the isolated app-shadow provider. The same explicit `--download-only` setup
prepares those dependencies; default validation never downloads them.

### 3. One canonical regression command

With the selected test environment ready:

```sh
validation/.venv/bin/python -m validation --profile offline
```

On Windows, substitute `validation/.venv/Scripts/python.exe`. The canonical runner
writes a new evidence directory. It executes tests and may generate local test
artifacts; this is intentionally a separate command from read-only preflight.
Use `validation/README.md` for separate interpreter options, supported profiles,
watch mode and authoritative exit codes.

To inspect saved reports without rerunning anything:

```sh
python -B -m deployment_readiness --validation-report /path/to/validation/results/RUN --format json
```

The consumer verifies supported report versions, latest/cycle consistency,
recorded plan and source-manifest hashes, required PASS/stability contradictions,
and rehashes the files *listed* in the source manifest. It respects the loop's
terminal status. It does not independently reconstruct Git's tracked/untracked
scope, find new unlisted source, rerun tests, authenticate logs, or re-probe
historical interpreters. A saved recorded PASS is therefore **informational**,
never fresh acceptance or deployment permission. Run the canonical command again
for current source/dependency-bound acceptance. No custom report format or
self-signed checklist can clear the physical gates.

### 4. Optional manual shadow exercise

After software validation, use the non-actuating session protocol documented in
[robot_llm/SESSION.md](../robot_llm/SESSION.md). Linux/WSL deterministic example:

```sh
printf '%s\n' \
  '{"op":"propose","raw":"{\"schema_version\":1,\"kind\":\"clarify\",\"question\":\"Which speed and duration?\"}"}' \
  '{"op":"close"}' \
  | validation/.venv/bin/python -m robot_llm.session --mode manual --allow-shadow \
      --capabilities pixel_robot/app/src/main/assets/policy_metadata.json
```

This is authored JSON, not a natural-language demonstration. Shadow outcomes
never represent physical motion or a verified stop. Do not feed model/web/camera
content into the trusted session-operation channel. A schema-1 legacy metadata
file cannot replace schema-2 shadow capabilities. The synthetic fixture in
`robot_llm/data/capabilities.json` is not a promoted physical bundle.

### App-integrated text-intent preview

The V4 app source adds an explicit text-intent shadow panel through a
service-owned request coordinator and an isolated HTTPS provider. See
[SHADOW-PREVIEW.md](../pixel_robot/docs/SHADOW-PREVIEW.md) for exact operator
consent, endpoint/text limits, lifecycle cancellation and the physical boundary.
Preflight does not open that page, start the app, accept network consent, or send
text to a model. A host Kotlin or browser-JavaScript pass does not establish
Android compilation, lifecycle behavior, live provider quality, or physical
stopping.

`LoadedPolicyAssets` captures copied metadata, ONNX actor/manifest, stride
reference and effective calibration bytes for the service and controller.
Its parsed contracts are separate per consumer because contract arrays are
mutable. This changes the runtime asset-loading boundary, not the bundled
V22 actor contract. The static bundle check reads the repository's bundled
calibration only. It cannot observe a saved app calibration override or verify
the snapshot actually loaded by a running app, and reports both as unverified.
Production tests must verify snapshot copying, per-consumer isolation and
identity binding; no saved file flag establishes current robot calibration.

### 5. Android build-only boundary

Building creates artifacts and may fetch Gradle/Maven dependencies; it is outside
read-only preflight and requires the toolchain/network setup to be authorized.
Keep the current full-SDK/license permission gate BLOCKED until the operator
has reviewed and approved the exact required terms and setup. Preflight never
accepts SDK licenses, and the presence of `android.jar` is not evidence that
license acceptance or installation was authorized. With that separate setup
approved, Android SDK 37 and a compatible Java/Gradle installation available, from
`pixel_robot/`:

```sh
./gradlew test assembleDebug assembleAndroidTest
python tools/check_elf_alignment.py app/build/outputs/apk/debug/app-debug.apk
```

PowerShell uses `.\gradlew.bat` instead. The repository's Windows convenience
command is `.\Install-PixelRobotDemo.ps1 -BuildOnly`; this returns before ADB
installation/device testing. Do not add `-SkipDeviceTests` to make a gate pass.
For a source-only host Kotlin test route, consult the current Pixel and validation
runbooks; host tests do not replace Android packaging or on-device tests.

**Stop before installation.** Installing APKs, granting USB permissions, changing
saved calibration/configuration, and launching a demo require explicit operator
approval. Do not execute the unqualified installer command as a setup probe.
An APK build, native-library alignment check, or ONNX parity result does not
verify USB timing, servo mapping, physical stop or safe walking.

### 6. Firmware, model, simulator and robot blockers

- Firmware build-only command, after approved dependency setup, is
  `pio run -e esp32dev` from `robot_dog/firmware/robot-dog-control/`. It can fetch
  packages and write `.pio` artifacts. Do not use upload/OTA/filesystem upload or
  device-monitor commands during setup. Record the actual dependency versions,
  board, binary hash and existing flashed identity before considering a change
- No endpoint is discovered or contacted. For already-collected, authorized
  model evidence, use the read-only qualification command below. Collecting new
  evidence needs approval for the exact endpoint, corpus data and inference
  resources; the authored corpus is not independently certified holdout data
- GB10/Isaac is untouched. A CPU test suite is not physics evidence. Inspect
  existing workloads and exact backend identity only in a separately authorized
  backend task; never start/stop workloads merely to clear preflight
- No USB/serial device is opened, even for discovery. Actual calibration,
  effective firmware servo settings, power, IMU orientation, feedback age,
  watchdog behavior, torque-off behavior and physical clearance remain unknown
- Leo operates the robot. The saved `calibrated:true` fields and earlier notes
  are historical file claims. Reconfirm them against the actual assembly;
  preserve known-working actors and do not widen ranges, torque or motion limits

Existing model report qualification (no inference is initiated):

```sh
python -m validation --profile live-model \
  --model-report /path/to/existing-live-report.json \
  --corpus /path/to/reviewed-holdout.jsonl \
  --capabilities /path/to/exact-capabilities.json \
  --criteria /path/to/reviewed-criteria.json --corpus-role holdout
```

Do not copy example criteria and treat their existence as approval. Full retained
completions and provenance are required; evaluator process exit 0 is not semantic
acceptance. See [QUALIFICATION.md](../robot_llm/QUALIFICATION.md).

## Pasteable Codex task

> Read AGENTS.md, deployment_readiness/README.md and validation/README.md. Run
> `python -B -m deployment_readiness --format json` in this checkout. Select the
> existing approved Python/test environments and run the complete canonical
> offline profile. Fix authorized source/test defects, rerun all required gates
> against the final source, and stop only on a source-stable offline PASS or a
> specific toolchain, input, permission or external-evidence blocker. Report
> offline acceptance separately from Android, firmware, model, Isaac and physical
> readiness. Preserve actor bytes, calibration, coordinate conventions,
> historical export identities and runtime limits. Do not auto-install packages,
> call endpoints, open hardware, alter device configuration, deploy, flash, arm,
> move, push, weaken gates, fabricate evidence, or refresh static-contract
> fingerprints just to hide source drift. Ask for the exact missing setup or
> authorization instead.

## What the static checks cover

- Strict bounded JSON with duplicate-key/nonfinite rejection; no evaluation of
  source code, model deserialization, ONNX execution or pickle loading
- Legacy NPZ byte hash, numeric NPY header dimensions/layer compatibility and
  metadata/calibration mapping. Header checks do not inspect every array value
- Pixel metadata-to-loader candidate identities, ONNX byte hash against manifest,
  V22 observation/history/action/current/posture/stride contracts, reference-vector
  dimensions and calibration. The ONNX graph itself is not parsed or run here
- Separate joint-coordinate conventions, semantic order and physical servo-ID
  mapping, finite limits, linkage declarations and IMU matrix structure. These
  validate files, not measurements or installed overrides
- A reviewed production-source fingerprint detects stale static-check assumptions.
  Python uses only selected contract definitions/constants as an AST digest;
  unrelated transport/session edits do not invalidate portable-math diagnostics.
  Kotlin normalizes CRLF/LF. Drift blocks only this static-review diagnostic,
  never unrelated offline setup or math tests. Review changed contract definitions,
  update the actual checks/tests if necessary, then update the reviewed fingerprint;
  no automatic refresh command exists. This is a freshness guard, not attestation
- Node presence is required when the canonical plan declares `pixel-web-ui`;
  its exact version is read as a literal `NODE_VERSION`, never by running Node
- SDK license acceptance remains unverified and BLOCKED even when a platform
  jar is present. The tool has no accept/install/permission override
- Tool versions are reported only where declared in source/package metadata;
  executable presence is never called a tested installation

## No-side-effect boundary and tests

```sh
python -B -m unittest -v deployment_readiness.test_preflight
```

Tests cover missing files/dependencies/tools, version/schema/source drift,
corrupt bundles, command/action/history/coordinate mismatches, calibration and
IMU failures, stale/partial/contradictory reports, unsafe paths and symlinks,
nonregular FIFO rejection before opening, and hostile NPZ object headers. A
snapshot test compares file contents and modification times before/after, forbids
process/socket calls, and checks that no project-runtime/optional imports occurred; a fresh CLI test checks stdout
JSON and no repository/home writes. CLI audit-hook tests reject write-open,
process, network, filesystem and environment mutation events.

The guarantee is scoped to this trusted Python implementation run with `-B`:
no application writes or external activity. Reading may update OS access times;
Python startup hooks, a compromised interpreter/native library, concurrently
replaced ancestor directories, and hostile custom import hooks are outside its
sandbox claims. Use a read-only/network-disabled container for stronger isolation.
