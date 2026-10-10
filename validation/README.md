# One command, source-bound validation

From the repository root, after installing the pinned test environment:

```sh
python -m validation
```

This runs every **offline** gate and saves JSON evidence plus readable logs.
It never installs packages, requests language-model inference, starts a backend, edits source,
commits, deploys, flashes firmware, opens a serial port, or moves the robot.
An offline PASS is not physical safety, model qualification, or deployment
readiness. The runner is Python standard library; BAML, Torch, the CPU ONNX runtime, Node, and the Java/Kotlin host toolchain are separately
required for their selected checks. Missing dependencies are **BLOCKED**, never
silently successful. Unsupported shell syntax checking is also BLOCKED.

## Set up once

Python 3.12, an existing Java 17+ runtime and **Node 24.19.0** on PATH are the tested setup baseline.
Install the exact Node version from the official Node.js distribution before
running the complete profile, or select an existing executable with `--node PATH`.
CI pins this version through `actions/setup-node`. The JavaScript gate uses Node's
built-in VM and authored DOM/fetch stubs; no npm install, browser, Android SDK or
WebView is required. Other Node versions and missing Node are BLOCKED, not skipped.
Create an isolated environment, install CPU Torch from its official index, then
install the other exact direct pins and prepare the checksum-pinned Kotlin/JUnit
cache from official Maven Central artifacts. These
are explicit installation commands and require network access; the runner does
not perform them. Transitive package versions are recorded in every report.

Linux/macOS shell (the pinned Torch wheel must support your platform):

```sh
python -m venv validation/.venv
validation/.venv/bin/python -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
validation/.venv/bin/python -m pip install -r validation/requirements.txt
validation/.venv/bin/python pixel_robot/tools/run_host_kotlin_tests.py --download-only
validation/.venv/bin/python -m validation
```

Windows PowerShell:

```powershell
py -3.12 -m venv validation/.venv
validation/.venv/Scripts/python.exe -m pip install torch==2.5.1 --index-url https://download.pytorch.org/whl/cpu
validation/.venv/Scripts/python.exe -m pip install -r validation/requirements.txt
validation/.venv/Scripts/python.exe pixel_robot/tools/run_host_kotlin_tests.py --download-only
validation/.venv/Scripts/python.exe -m validation
```

On a platform without a compatible pinned CPU Torch wheel, the CPU gates stay
BLOCKED; use a supported CPU environment rather than quietly changing versions.
Bash must be on PATH for the shell syntax gate (e.g. an existing Git Bash on
Windows). No PowerShell parser, Android SDK, Isaac or firmware toolchain is
implied by this check. Some required process-group regressions are POSIX-only;
native Windows reports BLOCKED for those skipped checks. Use Linux/WSL for the
complete currently verified offline profile rather than treating the skip as a
pass. Python 3.10+ is supported by the stdlib runner; selected
optional dependency wheels may have narrower Python/platform support.

To use already-installed environments without activation:

```sh
python -m validation \
  --baml-python /path/to/baml-venv/bin/python \
  --torch-python /path/to/torch-venv/bin/python \
  --runtime-python /path/to/runtime-venv/bin/python \
  --base-ref 27878e11
```

`--python` selects the base interpreter. `--baml-python`, `--torch-python` and
`--runtime-python` default to it. The runtime interpreter uses the authoritative
NumPy/ONNX pins in `robot_llm/requirements-runtime.txt`
(`numpy==2.2.6`, `onnxruntime==1.24.3`).
`--jvm-cache PATH` can reuse an already downloaded checksum-pinned cache during a
clean-checkout run; default is `pixel_robot/.tooling/host-kotlin`. No dependency
download is possible through the validation CLI. Keep the **venv entrypoint path**, not its resolved system-Python symlink.
`--base-ref` binds a locally available commit (default HEAD); nothing is fetched.

## Gates and honest scope

The deterministic offline plan covers:

1. Runner regression tests
2. AST-parse every nonignored Python source in the bound source manifest
3. Discover every `robot_llm/tests/test_*.py`, including session, qualification,
   and corpus suites; original baseline minimum 74, with named suites required
4. Discover both autoresearch/Qwen suites (minimum 41)
5. Discover control-center suites (minimum 25)
6. Run `training/test_evaluate_simple_dog_policy.py` and
   `training/test_export_v2_policy.py` (minimum 23)
7. Run the Onshape mate-planner suite (minimum 11)
8. Discover portable Pixel capture/data-tool tests, excluding exactly the named
   original-walk capture test and recording that boundary (minimum 16)
9. Run read-only deployment setup/preflight regression tests (minimum 65)
10. Discover the legacy 180-value Python policy runtime tests using NumPy and fake
    transports (minimum 87); no pyserial installation or serial port is required
11. Fresh-compile and execute production Kotlin host/JUnit suites with the pinned
    dependency cache (minimum 157, no ignored or assumption-aborted tests); report Java identity, source
    fingerprint, production/excluded classes and pinned jar hashes
    plus executed/finished test IDs. Required V4 suites cover immutable loaded
    assets, request coordination, provider isolation and protected local routes.
    The host HTTP regression server binds only loopback with fake callbacks.
12. Execute the real control HTML's shadow-panel inline JavaScript under Node with deterministic
    DOM/fetch stubs (minimum 48); record actual HTML/script bytes and test IDs. This checks
    preview-panel logic, not Android WebView rendering, device behavior or a live provider.
13. Run the separate CPU ONNX/Pixel runtime integration checks using the actual
    checked-in actor, reference vectors and production Kotlin math peer (minimum 20),
    followed by a retained 200-frame authored-feedback demonstration with explicit
    local policy-actor inference, no language-model inference, trusted Stop/revoked
    diagnostic-frame rejection, and no hardware
14. Run fixture evaluator and assert its exact deliberate-negative regression
    signature: 32 authored cases, 25 correct, 3 invalid raw responses, 1 timeout,
    2 wrong admits including 1 unsafe admit, and 1 repaired invalid candidate
15. Verify the Python socket/DNS guard rejects before DNS, and `bash -n` every
    nonignored `.sh` in the source manifest
16. Generate BAML from current source using pinned `baml-py==0.226.2` and
    `pydantic==2.11.7`, then run all 8 BAML render/parser checks
17. Run the eight existing CPU timing/reference/dynamics/critic/height/reward/
    evaluation/provenance pytest files (minimum 68 collected plus subtest reports)
    using `torch==2.5.1` and `pytest==9.1.1`
18. Generate and validate both standard/highlift gait diagnostics across the real
    14-command menu and 201 phase samples each
19. Require source, Git identity, Python dependency inventory and native toolchains to remain unchanged

The Kotlin gate is a host compile/test boundary, not an Android APK build,
Android lifecycle test or USB/device run. Its excluded-source list and compile-only Android API boundaries are retained.
Compiling Controller/ONNX classes against stubbed Android APIs and exercising
HTTP routes on host loopback does not mean those classes were exercised on Android.
Before the compute-only actor tests, the verified JVM command is prepared with a
separate bounded cold-compile budget. The prepared immutable class directory is
reused without compiling inside the per-frame RPC deadline.
CPU actor inference and reference parity likewise do not establish deployment
readiness or physical behavior. These host gates never clear the separate
Android, firmware, Isaac or hardware requirements.

The gait check verifies finite, complete diagnostics. It intentionally does
**not** turn residual clipping/headroom into a new acceptance threshold or alter
historical locomotion gates. Intentional unsafe fixture admits measure the
regression harness, not model performance. Actual test counts and test-ID hashes
are recorded; new discovered tests run automatically. Skips and expected failures
make required suites BLOCKED; zero/reduced collection cannot accidentally pass. The CPU pytest gate overrides
ambient config addopts and requires an executed passing call for every collected
test, so collection-only output cannot masquerade as a test run.

`--profile core` runs only stdlib checks and Bash syntax, including portable
Pixel data-tool fixtures and preflight regressions. It excludes JVM/Node/ONNX/runtime
dependency checks. It does not claim a full
offline pass. `--steps llm-unit fixtures` is an explicitly partial selection;
`--steps baml-unit` automatically includes fresh BAML generation. The report's
`full_offline_passed` is false for every partial selection. `--plan` prints the
fixed plan without checks or dependency probes. `--step-timeout SECONDS`
overrides each check's normal 120-second limit (CPU pytest/ONNX normally 300
seconds; fresh Kotlin compile/test normally 420 seconds).

## Fix and rerun with Codex

```sh
python -m validation --watch --max-cycles 10 --watch-timeout 1800
```

The first run starts immediately. On failure/blockage the process waits for a
source, Git identity, Python dependency inventory, Java/jar or Node prerequisites, or
supplied live-evidence input change, then reruns the **whole selected plan** against the new snapshot. Codex
or the operator can inspect the JSON/logs, make authorized local fixes, and let
the runner notice them. No model, shell hook, arbitrary command, code edit, or
commit is triggered by the runner. It stops on a complete requested-scope PASS,
the maximum number of runs, the watch timeout, or Ctrl-C. Reaching a limit never
converts an unresolved gate to PASS. `--rerun-unchanged` explicitly opts into
bounded retrying at `--interval` seconds even without changes; default is 2.
Provenance collection uses its own bounded subprocess probes, so shutdown can
include a short final identity/dependency collection after the watch deadline.

Example Codex task (ordinary authorized engineering work, no runner privilege):

> Run `python -m validation` with the configured test interpreters. Read
> `latest.json` and failed check logs. Fix source/test defects within the requested
> scope, rerun the complete offline profile, and stop only on a source-stable PASS
> or a concrete environment/authorization blocker. Do not weaken tests or gates,
> fabricate evidence, initiate network/model calls, use hardware, or deploy.

## Evidence contract

The default destination is a new `validation/results/<UTC timestamp>/`. Use
`--output PATH` for an absent or empty directory; existing evidence is never
overwritten. These generated directories and the local `.venv` are ignored.

- `cycle-NNN/report.json`: schema version 1, plan, status, per-check evidence,
  source/base/HEAD hashes, interpreter binary identity and complete installed
  package inventories before and after the cycle; native toolchain snapshots bind
  Java launcher/release identity, checksum-pinned jar bytes, and Node's exact
  version plus selected binary hash before and after each run
- `source-before.json` / `source-after.json`: per-file SHA-256 manifests, including
  deleted tracked files and nonignored untracked source; no file contents
- `<check>.json` and `<check>.log`: independently parsed structured outcome and
  combined stdout/stderr; exit 0 without a valid matching result is a failure
- `fixtures-artifact.json`, `gait-probe-artifact.json`, `pixel-jvm-artifact.json`,
  `pixel-web-ui-artifact.json`, `pixel-runtime-artifact.json`, or `live-model-artifact.json`:
  substantive evidence, individually hashed
- `summary.txt`: readable cycle summary
- `latest.json`: most recent cycle report; `loop.json`: authoritative loop exit,
  stop reason and cycle history (a watch may time out while latest is BLOCKED)

Statuses are PASS, FAIL, BLOCKED, TIMEOUT; SKIPPED is reserved for unrequested
external gates. Exit codes are 0, 1, 2 and 3 respectively. `--plan` exits 0 without
claiming validation. Source edits while a cycle runs make it BLOCKED even if
every check passed. Evidence binds the worktree bytes, not merely a commit ID.
Gitignored generated files and the explicit output directory are excluded from
source hashes, so evidence creation does not spuriously invalidate itself.
Do not place authoritative source in ignored output or venv directories. Source
symlinks are BLOCKED because their external targets are not bound by the Git file
manifest; use real source files in the review worktree.

This is local reproducibility evidence, not a signature or tamper-proof
attestation. Dependency inventory hashes bind installed versions, not every
installed package byte. The runner records no environment variable values or
credentials. Children receive a small clean environment; model keys, proxies,
BAML tracing credentials, PYTHONPATH and pytest plugin options are not forwarded.
Review artifacts/logs before sharing: tests or deliberately supplied model
reports can still contain source-specific text or sensitive utterances.

Python audit hooks reject socket connections, DNS and UDP sends in child checks.
This is defense in depth, **not an OS network sandbox** for arbitrary native
extensions, malicious source, or subprocesses. Fixed trusted checks contain no
external network commands; BAML generation is local. The Kotlin HTTP regression
tests use an ephemeral loopback-only server with fake callbacks; Node UI tests
replace browser fetch with deterministic stubs. Neither contacts a model provider.
Use OS/container network isolation
for stronger assurance. POSIX timeouts freeze the launched process and snapshot/terminate its observed
descendants, including children with their own sessions, before killing the
owned process group. A successful command that leaves same-group descendants
is killed and fails. This is cleanup for trusted tests, not a sandbox against
deliberate daemonization; it relies on the standard `ps` utility for detached
child ancestry and falls back to group cleanup when that utility is unavailable.
Linux signalling binds `/proc` start-time identity; other POSIX systems use the
reported process start time. Only the launched subtree is eligible; deliberately
reparented/double-forked processes are outside this trusted-code cleanup guarantee. On Windows,
`taskkill /T` handles still-running parents at timeout, but already-orphaned
children cannot be reliably discovered; use an OS job-object/container boundary
when stronger process isolation is required. No timeout is a physical watchdog.

## Read-only live model qualification

The runner **never starts inference**. An operator may separately use the existing
explicit-opt-in evaluator workflow in [EVALUATION.md](../robot_llm/EVALUATION.md),
including completion retention for independent rescoring. Supply an existing
report and explicitly reviewed criteria:

```sh
python -m validation --profile live-model \
  --model-report /path/to/live-report.json \
  --corpus /path/to/operator-reviewed-holdout.jsonl \
  --capabilities /path/to/capabilities.json \
  --criteria /path/to/reviewed-criteria.json --corpus-role holdout
```

This calls `robot_llm.qualify` with no endpoint or network opt-in, validates its
structured semantic result, and hashes every supplied file before/after. A
synthetic fixture report, missing completions, stale corpus/schema/policy hashes,
inadequate coverage, wrong/unsafe admission or gate failure cannot pass just
because an evaluator exited zero. Use `--python` pointing at the pinned BAML
environment for reports containing BAML comparisons. Source binding means the
report was **requalified against the current implementation**; it does not prove
which source checkout originally produced a preexisting inference report.
Holdout independence and server provenance remain operator attestations.

`--profile isaac`, `android`, `firmware`, or `hardware` records a required BLOCKED
gate explaining the outstanding work and makes no backend/device calls.
`--profile all` runs offline checks plus read-only live qualification if inputs
were supplied, and keeps those physical/integration gates BLOCKED. There is no
"approve all" option and no invented simulator or hardware command.


## Original recorded Pixel capture is a separate gate

`--profile pixel-capture` runs only the existing golden-regression test
`test_fit_sim_from_run_data.FitSimFromRunDataTest.test_downloaded_walk_matches_calibration_golden`.
It requires the original local file
`training/diagnostics/pixel-runs-20260829/robot-run-20260829-141035-982-d14ad623.jsonl`.
No download, robot query or generated substitute is used. If absent, this profile
is BLOCKED. If present, its bytes are hashed before and after the test, including
while watching for changes; the ignored input cannot silently change midrun.

The ordinary offline Pixel data-tool step discovers all tests, requires that
this exact external test still exists, excludes only that fixed ID, and reports
`excluded_external_tests` plus discovered/selected/run counts. All remaining
portable tests are required; any other skip still blocks. `--profile all`
includes the real-capture gate. Passing the portable tests does not assert that
a recorded walk was analyzed. Passing the retained-capture regression verifies
those golden measurements only, not authenticity of the file or present robot
readiness.


## Native firmware host assertions are an explicit separate profile

When the tested local ArduinoJson headers and a C++ compiler are already installed:

```sh
python -m validation --profile firmware-host \
  --firmware-json-include /path/to/ArduinoJson/src
```

The default compiler is `g++`; `--firmware-compiler` selects an existing host
compiler. The gate accepts the tested ArduinoJson 7.0.4 or 7.4.3 headers, runs the
repository's actual native C++ harness with extracted production policy handlers,
UART framing and watchdog functions, and records version/source evidence. Bus,
clock and transport dependencies are fake. Header-tree and compiler hashes are
bound before/after and participate in change watching. Missing headers/compiler
are BLOCKED, never downloaded or installed by this command.

This optional profile is separate from the practical default offline plan. Its
PASS is a native assertion-harness result, not an ESP32 build, board execution,
physical timing, transport certification or firmware deployment. Any separately
recorded full ESP32 compilation is additional compile-only evidence. The full
`--profile firmware` remains BLOCKED for device/transport/watchdog acceptance,
and no profile flashes a board or opens serial. `--profile all` includes this
host gate too, requiring the explicit include path.
