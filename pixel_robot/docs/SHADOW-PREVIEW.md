# Pixel text-intent SHADOW preview

The app now wires a text provider to the **service-owned Kotlin admission
component**. This is an intent preview, not a motor or policy-actor execution
path. The existing physical controller remains independent. Android and local
provider checks are recorded in [INTEGRATION-STATUS.md](INTEGRATION-STATUS.md).
No robot-connected USB write or movement was performed.

## Operator flow

1. Open the existing local control page served by the running Pixel app. The
   **Text intent preview** panel is separate from the existing physical controls.
2. Enter the exact authorized OpenAI-compatible base URL, using **HTTPS** and a
   path ending in `/v1`, and the exact model name. Plain HTTP is also accepted
   for literal phone-loopback addresses `127.0.0.1` and `[::1]` only; `localhost`
   and LAN addresses are rejected. URL userinfo, query strings
   and fragments are rejected. There is no inferred/default
   endpoint, model discovery or automatic request.
3. Enter one request of at most 2,000 Unicode code points. Check **Send this text
   to my selected endpoint when I submit**. The selected model name, submitted
   text and static intent-only system prompt are sent to that endpoint's
   `/chat/completions` route. Bundle bytes/hashes, calibration, feedback, robot
   state, host envelopes and grants are not sent.
4. The current bundle is unpromoted and transport-unverified. A velocity preview
   requires the separate **Allow an unqualified bundle for this SHADOW preview
   only** checkbox. This is a per-request shadow opt-in, never permission to
   execute motion. Omitting it can still produce clarification or unsupported
   outcomes; a velocity candidate is rejected.
5. Submit explicitly. Inspect the host outcome and its preview, clarification or
   rejection reason. An accepted preview expires after its admitted duration,
   at most 1,000 ms for this capability. Expiry clears the preview.
6. **Cancel preview** invalidates pending/active shadow work. **Stop SHADOW
   session** stops only shadow work. The existing physical **STOP** remains the
   independent physical stop-request action and also revokes shadow work.

Endpoint and model changes clear network consent. Configuration and request text
are not persisted by this feature. The provider connection has no credential
field, cookie jar, cached authentication, redirect following or cleartext
fallback. Authentication or cleartext beyond literal loopback needs a separately
reviewed connection design; do not paste secrets into the
URL, model name or text. Do not bypass TLS certificate warnings.

## Service integration and identities

`LoadedPolicyAssets` captures immutable copies of metadata, ONNX manifest, ONNX
actor, stride reference and **effective** calibration bytes (including an app
calibration override). The physical `PolicyController` and `OnnxPolicy` consume
this same snapshot. Parsed contracts are separate per consumer because existing
`PolicyContract` arrays are mutable. `ShadowCapabilities.fromBundle` validates
and hashes those exact bytes. Asset identity does not establish provenance,
physical promotion, motor safety or authorization.

`RobotService` constructs `ShadowRequestCoordinator` with
`SystemClock.elapsedRealtimeNanos() / 1_000_000`. Its coroutine polls every 50 ms
independently of the provider callbacks and UI status collectors. This is a
scheduled software check, not a demonstrated device scheduling/watchdog latency.
Every admission, submission and poll also checks the original monotonic deadline.

The production API is deliberately narrow:

- `beginShadowRequest(text, allowUnqualifiedMetadata)` returns an opaque
  `ShadowRequestHandle`. A trusted, explicit operator request creates the
  original envelope and shadow grant **before** provider work
- `submitShadowResponse(handle, originalCompleteIntentJson, claimPublication)` submits the
  original complete provider string to the strict intent decoder. Handle object
  identity is required. The provider cannot supply or reconstruct a grant,
  deadline, capability or source identity
- `failShadowRequest(handle)` reports a failed provider operation only while
  that exact request is still pending
- `cancelShadowRequest()` revokes the current source; `stopShadowPreview()`
  stops the shadow session. A subsequent request requires a new explicit
  operator submission; completion callbacks cannot reset it
- `shadowStatus` is the coordinator's `StateFlow<ShadowServiceStatus>`. The
  coordinator publishes state atomically rather than copying an old callback's
  snapshot into newer service state

A new explicit request supersedes both pending and admitted shadow work. The
host-owned envelope has an absolute 10-second ceiling including any admitted
intent duration. **Pending provider results expire at 8 seconds from host
issuance**, an earlier host-monotonic cutoff checked before and after admission;
the pending status reports that earlier deadline. Neither provider completion
nor an execution-thread delay extends either deadline. The provider also has an
independent publication timer. The provider must atomically claim publication under the coordinator
lock, winning against timeout/cancellation, before the host can admit its bytes.
A timer failure callback arriving later cannot turn a lost claim into admission.
Platform DNS or network teardown may take longer to exit; the single bounded
worker prevents replacement-thread growth, while expiry and publication fencing
remain independent of that I/O completion. An accepted
intent's duration must fit entirely within the remaining original deadline.
Provider work is bounded and cancellable, with identity/generation fences for
late completion and replacement. Expiry cancels only the original handle, so an
old timer cannot cancel a newer request.

The service revokes shadow work on manual motion/posture updates (including the
HTTP manual-command callback), manual policy/stand requests, physical STOP,
reconnect, USB loss, native-activity backgrounding, and service destruction.
The browser panel also cancels on hiding/leaving its page. Browser notification
may be lost when its process dies; the independently polled original deadline
still applies. Destruction closes the coordinator, cancels provider work and
stops the timer. Service recreation does not restore a grant or pending request.

## Local HTTP boundary

The existing local server accepts these separate routes:

- `GET /api/shadow/status`
- `POST /api/shadow/request`
- `POST /api/shadow/cancel`
- `POST /api/shadow/stop`

Shadow routes require the random per-server token embedded in the local page,
a loopback Host, and same-origin checks. The token is an in-memory browser
request fence, not remote-user authentication or persistent access. There is
no provider callback route, serialized handle route, actuator route or grant
field accepted from model output. Do not expose this control server outside its
existing loopback binding. Existing physical routes retain their distinct
manual purpose and never consume the shadow preview.

The provider uses a static intent prompt and JSON-object response mode, followed
by the independent strict host decoder. This is not a BAML runtime or a claim
that the provider enforces the host schema natively.

The request parser bounds header/body sizes and reads Content-Length in bytes,
rejects duplicate/ambiguous framing and invalid UTF-8, and performs no command
on a malformed request. Provider responses are bounded to 65,536 bytes and must
be one complete non-streamed assistant choice ending normally, containing one
strict intent object. Tool calls, truncated output, malformed/duplicate JSON,
extra intent authority fields and repaired/prose-wrapped answers are rejected.

Control connections have a 3-second lifetime, including queued reads/writes.
Downloads use a separate bounded queue and a 30-second deadline so a stalled
large transfer cannot occupy the control pool. A download that exceeds that
limit must be retried. These bounds are resource-isolation measures, not an
emergency-delivery guarantee or physical stop acknowledgement.

## Status is evidence of admission only

The JSON has separate provider progress and host state. Host states include
pending, shadow_accepted, clarify, unsupported, rejected, expired, cancelled,
stop_requested, unavailable and closed. An accepted preview gives named
forward/lateral/yaw values and a duration; posture is explicitly neutral.

Every status declares:

- `mode: "SHADOW"`
- `physical_execution: false`
- `physical_stop_confirmed: false`
- `policy_actor_inference: false`

No service preview calls `startPolicy`, `standAtCapturedPose`,
`updateMotionRequest`, `updateFullRequest`, ONNX actor inference, arming or the
USB transport. A model `stop_request` cannot stand in for physical STOP. Neither
an admitted preview nor the word "completed" in provider progress establishes
motion, measured distance, physical success or torque-off.

## Verification and remaining gates

Run the real production Kotlin host suite offline as described in
[HOST-TESTS.md](HOST-TESTS.md). `ShadowRequestCoordinatorTest` exercises explicit
opt-in, opaque/foreign handle rejection, latest-request replacement, independent
pending/admitted expiry, exact-deadline callbacks without a timer tick, no
renewal, every lifecycle cancel reason, slow/late replies, stop/recreation,
clock rollback, exact provider-cutoff rejection, barrier-controlled
publication/timeout ordering, original strict intent strings and text bounds.
`LoadedPolicyAssetsTest` verifies actual byte hashes, copy defenses, per-consumer
contract-array isolation, effective-calibration binding and actor tampering.
The provider and route tests use controlled fakes/byte fixtures, never a live
model. Test counts and exact final source fingerprints belong to the generated
validation report rather than this document.

The host suite compiles and runs these production pure seams. It compiles the
real `PolicyController` and `OnnxPolicy` boundaries without executing their
Android-bound methods or loading Android ONNX native libraries. Loopback tests
also execute the real `RobotControlServer` with injected page bytes and fake
callbacks; its Android `AssetManager` page-loading path is not executed.
The real `RobotService` and USB adapter now also compile against explicit,
throwing Android/AndroidX/driver API shims. `MainActivity` is still excluded.
Those shims catch internal Kotlin wiring; they do not validate actual Android
API compatibility, resources, USB drivers or lifecycle callbacks. A host pass
is **not** a full Android application build or device execution. An SDK/Gradle app build, installation and separately authorized
device/provider evaluation remain distinct gates. The HTML has separate offline
DOM-stub tests; these are not rendered-browser QA. In this environment the cloud
browser returned `net::ERR_BLOCKED_BY_CLIENT` for the local preview URL, so no
browser rendering pass is claimed.

The earlier Python/JVM compute bridge remains an independent actor-computation
verification path; this app preview does not claim to execute that bridge.
A distinct [one-shot physical operator path](PHYSICAL-INTENT.md) now supplies
separate authority, bounded leased consumption and final dispatch checks. It
never consumes a shadow grant or preview. The shipped bundle remains blocked
there. Real Android/device/provider, firmware acknowledgement/watchdog,
calibration/provenance, timing and physical-trial acceptance remain separate
requirements; see [FIRST-ROBOT-TEST.md](FIRST-ROBOT-TEST.md).

All HTTP mutations now require the local token/origin boundary, including
legacy manual controls. Manual Start/Stand additionally require the current
`expected_manual_generation` from `/api/status`; STOP invalidates queued starts.
Existing clients must fetch a fresh page token and generation rather than
relying on the former unauthenticated manual POST routes. The Android lifecycle
test source has been updated for that contract, but has not been device-run.

## Local reconciliation status

The prior local Android and device integration evidence remains in INTEGRATION-STATUS.md and the checkpoint handoff. It predates this V5 reconciliation and does not validate the new transport protocol. The one-second operator-only PhysicalCommandAuthority remains available alongside the separate one-shot PhysicalIntentAuthority. Both require independent authority; the bundle remains unpromoted and physical transport unverified. Current reconciliation results are recorded in V5-RECONCILIATION.md.
