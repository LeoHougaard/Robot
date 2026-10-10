# Current Pixel compute-only integration

This closes a software integration gap: an admitted `ShadowSession` body-velocity
intent now reaches the **real current Pixel policy computation**, rather than
ending at an isolated admission result. There is no robot transport, device
discovery, arming method, language-model call or physical mode in this demo.

The executable chain is:

1. Complete authored JSON proposal → existing strict contract and ShadowSession
2. Actual supervisor outcome → one-use, host-clock-fenced frame ticket
3. Actual Kotlin `PolicyFrameSession.observation` → 428-value observation
4. Checked-in `policy_actor.onnx` → ONNX Runtime CPU → twelve actor residuals
5. Actual Kotlin `StrideSession`/`PolicyContract` action processing and
   `RobotCalibration.servoTargets` → discardable diagnostic values
6. Fresh ticket/deadline/sample/capability check before returning the diagnostic

The JSONL JVM peer compiles repository production math, without a copied Python
observation builder or stride implementation. Its `complete` operation means
only `synthetic_host_only`; it is not a USB acknowledgement. The excluded Android
controller, serial transport and device lifecycle do not run in that process.

## Run offline

Python 3.12, Java 17+, and the explicit dependency setup below are the tested
host configuration. Downloads are separate, opt-in setup, never a demo action.

```sh
python -m venv .venv-runtime
.venv-runtime/bin/python -m pip install -r robot_llm/requirements-runtime.txt
python pixel_robot/tools/run_host_kotlin_tests.py --download-only
python pixel_robot/tools/run_host_kotlin_tests.py
.venv-runtime/bin/python -m robot_llm.runtime_demo \
  --allow-shadow --allow-unqualified-metadata
```

Use `ROBOT_PIXEL_JVM_CACHE=/existing/jar/cache` to select an existing JVM
dependency cache; no network fallback occurs. Before issuing a shadow request,
the demo prepares the verified JVM command with a separate 240-second cold-build
budget (compiler limit 180 seconds). Only then does it start the peer, whose
per-operation timeout is 5 seconds. Compiler preparation and peer cleanup own
their POSIX process groups; a cold build cannot silently extend an issued lease. The actor dependencies are pinned
to NumPy 2.2.6 and ONNX Runtime 1.24.3. This CPU runtime differs from Android's
ONNX Runtime dependency; a CPU pass does not establish device parity or timing.

The default demo runs 200 fixture frames. It checks all eight exported actor
reference vectors, the first 50 zero applied actions, subsequent nonzero action
processing, exact lease expiry and trusted STOP fencing of a pending diagnostic ticket. Its clock advances deterministically by
20 ms per completed synthetic frame; **this is not a measured 50 Hz runtime or
hardware timing result**. The report records raw asset hashes, dependency
versions, dimensions, cold-start/fixture-run wall times, evidence type and false physical-execution/stop flags. The explicit report fields are `language_model_inference: false` and `policy_actor_inference: true`.

The manually authored request is forward 0.02 m/s for 4,000 ms. This requires an
explicit 5,000 ms test capability limit. It does not change the default 1,000 ms
session limit or metadata. Requested distance is not measured displacement.
The fixed target is used as the initial applied command, matching the existing
controller's initial command convention. This demo does not validate changing-
command smoothing, a world-goal controller, localization or obstacle handling.

Synthetic samples have stationary joints and gravity plus explicit current
missingness. The next frame's acknowledged action is an **ideal synthetic
acknowledgement** of the previous computed action. There is no simulated motor
dynamics, real sensing, robot-state confirmation or physical success claim.

## Asset identity and version separation

`PixelActor` reads the ONNX bytes, deployment manifest, metadata, stride
reference, calibration and exported actor reference vectors once. It checks the
manifest's ONNX hash, profile and source-weights identity, current 428-input
`cad_drives_v1` contract, reference hashes and numerical parity. The JVM's actual
loaded metadata/reference/calibration hashes must match those bytes. Diagnostic
target arrays must carry the calibration's exact policy-order servo IDs.

The unchanged current bundle remains explicitly unpromoted and
transport-unverified. Its `weights_sha256` describes source weights; the
manifest's `onnx_sha256` binds the actual portable model bytes. A hash confirms
identity, not provenance, signature, authorization or safety. Calibration flags
and promotion fields are metadata claims, not measurements made by this code.

The separate `robot_dog/policy_runtime` NumPy actor is the legacy 180-input
assembly-1 bundle. It is deliberately not substituted for the current Pixel
428-input actor. Pixel's body-frame three-axis contract remains distinct from
the legacy helper's world-pose goal controller, which needs fresh localization.

## Cancellation, stale data and errors

- `ShadowCommandBridge` owns its session; it never admits serialized events as
  authority. It rechecks the session's real supervisor result at frame start
  and diagnostic completion
- One frame may be outstanding; a ticket is a one-use object identity. Request
  replacement, cancellation, stop, close and capability changes fence old work
- Host-clock deadlines include compute time. Future, duplicate, reordered or
  stale sample identities fail closed. The default 40 ms sample-age ceiling is
  a software test limit, not a verified robot watchdog
- Slow computation runs outside the bridge lock, so stop/cancel can invalidate
  it without waiting for actor execution. A late error cannot cancel a newer
  ticket. Failed actor/JVM work cannot become a diagnostic success
- JVM pipe input/output sizes and operation waits are bounded. POSIX cleanup
  owns the process group and joins readers/writers before closing pipe streams.
  The tested process lifecycle is Linux; native Windows descendant cleanup is
  not asserted. Use WSL for this host demonstration on Windows
- There is no automatic restart of a failed JVM peer. Construct a fresh runtime
  explicitly. A new admitted request resets Kotlin history, filters and clock
- Returning a diagnostic is a compute result at its final admission check, not
  a persistent command, motor heartbeat, stop acknowledgement or safety token

## Tests

```sh
python -m unittest robot_llm.tests.test_runtime_bridge robot_llm.tests.test_runtime_process -v
.venv-runtime/bin/python -m unittest robot_llm.tests.pixel_runtime_checks -v
```

The stdlib tests cover stale/deadline/cancel/stop/replacement races, forged and
replayed tickets, late providers, invalid operator/proposal data, blocked pipe
writes, dead peers, partial/oversized responses and descendant cleanup. Optional
integration tests require real installed dependencies; they never skip to a mock
actor. They exercise actual observation command slots, actual reference parity,
startup hold and subsequent stride, wrong bundle/coordinate/hash/servo identity,
nonfinite inputs, actor exceptions, invalid sample data, cancellation after real
inference and expiry while computation is in flight.

## Remaining Codex/operator wiring

1. Run canonical validation on the final combined checkout. Preserve the exact
   current asset hashes; do not replace/promote actors to make a test pass
2. Independently verify and authorize the existing model endpoint before live
   **shadow-only** sessions and model qualification. This demo uses authored
   intent bytes and provides no evidence of language understanding
3. Connect the trusted operator UI and any real model adapter to the existing
   session protocol. Keep model output restricted to intent bytes; never feed
   camera/model output into trusted operator operations or grant fields
4. Keep the current compute-only bridge separate from the device's control
   entry point. Any later actuation adapter needs separately reviewed explicit
   operator authority, independent stop/watchdog ownership and fresh verified
   sensor/transport state at the final write boundary. A shadow grant or this
   demo passing is never sufficient authorization
5. Validate Android build/device behavior, exact deployed bundle identity,
   motor-disabled transport, firmware loss/stop behavior and intended physical
   acceptance separately. Only the operator may authorize physical trials

No policy weights, source metadata, calibration, historical runtime semantics,
backend services or hardware are changed by this bridge.

## Android shadow integration and remaining physical work

The phone does **not** host the Python `ShadowSession` or the JVM test process.
Those remain the host-side executable verification path. The Android app now
has a separate, service-owned **shadow-only text preview** integrated into its
existing local control page. See
[SHADOW-PREVIEW.md](../pixel_robot/docs/SHADOW-PREVIEW.md) for the actual operator
flow, explicit HTTPS or literal-loopback endpoint consent, cancellation, and evidence boundaries.
No shadow outcome is consumed by the physical controller.

### Android-local pure admission is implemented

`pixel_robot/.../policy/ShadowIntentSession.kt` now supplies the actual pure Kotlin
admission/source/lease component. `ShadowIntentDecoder.kt` is an independent,
bounded, flat JSON decoder; it does not rely on Android/JVM `org.json` behavior
for duplicate keys, numeric coercion, trailing data or Unicode handling.

The host loads `ShadowCapabilities.fromBundle` from actual metadata, manifest,
ONNX, stride-reference and effective-calibration bytes. The existing production
contract/reference/calibration validators and candidate identity checks run before
the session is created. All five byte identities and the duration limit bind its
capability digest. That software check grants no physical authority.

The concrete API is:

- Construct `ShadowIntentSession(capabilities, clockMs, allowShadow = true)`;
  on Android supply `SystemClock.elapsedRealtimeNanos() / 1_000_000`
- `beginRequest(sourceId, ttlMs)` issues a host-owned envelope before inference
- `grantFor(envelope, allowUnqualifiedMetadata, expiresAtMs)` is an explicit
  trusted-host **shadow-only** grant call; raw model JSON cannot issue one
- `submitRaw(envelope, originalCompleteResponse, grant)` strictly decodes and
  admits the intent, or `submit` accepts the equivalent validated typed intent
- `beginFrame(sampleSequence, observedAtMs)` issues a one-use compute ticket;
  perform slow computation outside the session lock, then `finishFrame(ticket)`
  returns a tagged `ShadowMotionDiagnostic` containing the existing production
  `MotionRequest`, with explicit named axes and neutral posture
- `failFrame`, `cancelSource`, `replaceCapabilities`, `poll`, `stop`, and `close`
  fence old computation and provider results; STOP remains latched

The component has no reference to a controller instance, USB, socket, UI route,
arming call or physical writer. Its ticket is a pre-publication **validity**
check, not permission to send motor frames. There is no timer thread: the owning
host must poll, and physical watchdog design remains separate.

JVM tests exercise this actual component, including 32 shared authored intent
vectors also executed by the Python session. These demonstrate agreement on the
specified cases, not exhaustive cross-language equivalence. Intent whitespace
and Unicode code-point length follow the Python contract explicitly. Intentional
differences: Kotlin grants are opaque, one-use-identity-checked, request-specific
host objects; its digest binds actual current bundle bytes, and is not the
Python metadata-only digest. Tokens and monotonic timestamps cannot be exchanged
between these two implementations. `beginRequest` follows the supervisor's
pending-supersession semantics; cancel an active source explicitly before a new
request if implementing the Python interactive session's latest-request-wins UI.

### Completed app-side shadow wiring

The local page/provider adapter, opaque service-owned request handles and grants,
independent expiry polling, and manual/background/disconnect/STOP revocation are
implemented. The isolated provider returns original complete content to
strict admission; endpoint/model/text and per-request consent are explicit.
The page displays intent previews only, not policy-actor or physical execution.
LoadedPolicyAssets binds shadow capabilities to the same immutable source bytes
used by the controller/ONNX loader. Host tests exercise the pure coordinator and
provider seams; they do not establish a full Android build, on-device lifecycle,
real inference endpoint compatibility, or physical transport behavior.

### Operator lease and device acceptance

The Android controller now has a separate operator-owned physical lease. Both
operator approval and verified physical readiness are required. The lease binds
the loaded asset identity, named forward/lateral/yaw request, neutral posture,
generation and finite monotonic expiry. Controller operation cancellation,
lease identity/deadline and original feedback freshness are checked at the
actual bounded serial write. Expiry invokes the independent stop/disarm path.
No model callback or shadow grant creates this authority or starts a controller.

The USB transport now has one owned bounded writer and ordered background
teardown. Its completion means driver-written with firmware receipt unknown.
Host tests verify ordering; the Android build and six non-actuating Pixel tests
passed. See [integration evidence and remaining gates](../pixel_robot/docs/INTEGRATION-STATUS.md).

Robot-connected communication, firmware acknowledgements, watchdog/torque-off,
50 Hz timing, reconnect and approved physical trials remain outstanding. Keep
the bundle unpromoted and transport-unverified until those acceptance checks
pass. The Pixel is currently attached to the computer, not the robot.

The current joined tests already cover the production observation, actor,
stride and calibration computation, so those parts should not be reimplemented
for the UI integration. Goal navigation is additional work: `goal_controller.py`
uses world `x/y/yaw`, needs fresh external localization, and is not included in
the accepted body-velocity language contract.
