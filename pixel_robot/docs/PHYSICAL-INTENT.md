# One-shot physical AI operator panel

This panel is an experimental, separately authorized physical-command path for
the Pixel-controlled 12-servo robot dog. It is not a rover controller. Unlike
[SHADOW preview](SHADOW-PREVIEW.md), an admitted physical command can affect an
already running policy controller. Neither opening this page, clicking Enable,
nor receiving an AI result starts or arms the controller.

**The shipped policy bundle remains blocked.** Its qualification metadata is
unpromoted and transport-unverified. Preserve that result until separately
reviewed evidence qualifies the exact installation. Editing metadata to bypass
the gate does not create that evidence. See
[FIRST-ROBOT-TEST.md](FIRST-ROBOT-TEST.md) for staged commissioning.

## Operator sequence

1. Open the existing local Pixel control page. Read the **One-shot AI motion
   request** panel and its blockers. Bundle eligibility alone does not certify
   controller readiness, measured movement, stop acknowledgement or safety.
2. Enter the exact authorized HTTPS OpenAI-compatible base URL ending in `/v1`,
   exact model name, and one request of at most 2,000 Unicode code points. No
   endpoint, model or provider discovery is automatic. URL credentials, query
   strings and fragments are rejected. Credentials are unsupported; do not put
   secrets in any field.
3. Review the data-consent statement. Submitting sends the entered text, model
   name and fixed intent-format instruction to the chosen endpoint's
   `/chat/completions` route. The provider may retain these. Camera frames,
   telemetry, calibration, bundle bytes/hashes, host capabilities and grants are
   not sent. The page does not persist configuration or text.
4. Only after the independent physical qualification and commissioning gates
   pass, manually start the reviewed controller with neutral targets and fresh
   complete feedback. The operator owns this separate physical action.
5. Check both the data-consent checkbox and physical-risk acknowledgement, then
   click **Enable one physical AI
   request**. This is a host-checked, single-use enable with an original
   monotonic authority deadline of at most 10 seconds. It cannot be inherited
   from a preview or restored by page navigation.
6. Click **Submit one physical AI request** without editing the fields. A
   request consumes the enable, including failed, clarification or unsupported
   outcomes. Provider publication has an 8-second ceiling and must also fit
   inside the original authority deadline. An admitted command lasts at most
   1,000 ms, subject to the loaded capability limits and remaining authority.
7. Inspect the host state and admitted named-axis command. These show admission,
   not measured motion, travelled distance, task completion or confirmed stop.
   Expiry/cancellation requires a new reviewed manual operation and explicit
   enable before another physical request.

Prepare the endpoint/model/text **before** enabling. Manual controls clear both
checkboxes, so check them after the manual start. Editing any of the fields clears
network consent and revokes enable or pending work. Withdrawing consent or the
risk acknowledgement also revokes active authority. Every new request requires
fresh data consent and a fresh explicit enable; repeated submit clicks do not
create multiple requests.

**Cancel + disable physical AI** revokes authority and requests a stop of the
owned operation. The independent **Stop + torque off** control remains
available. Neither label nor HTTP success confirms firmware acknowledgement,
torque-off or mechanical stopping. Keep the independent physical safety
arrangements required by commissioning.

## Interruption and recovery

Manual start, stand, reconnect, motion/posture input and physical STOP fence the
panel's pending callbacks. Service lifecycle/link checks also revoke authority.
Hiding or leaving the page sends a token-protected keepalive cancellation;
browser/process teardown can lose that notification, so it is not a watchdog.
Back/forward restoration clears both consents and local authority and requires
Cancel + disable before a fresh enable.

A request/enable timeout, malformed response, changed host generation, foreign
request identity or unavailable status disables local submission. The panel
never reconstructs permission from a later status GET. If cancellation cannot
be verified, it keeps the warning and the independent cancellation controls
available. Refreshing status does not clear that warning; explicitly retry
Cancel + disable after the service is available. A timeout cannot establish
that no movement occurred. Host deadlines and transport safety checks remain
independent of the browser.

## Local API contract

All four routes retain the loopback Host, same-origin and per-server
`X-Pixel-Shadow-Token` boundary. Sharing this request-fence header with SHADOW does
not share authority. Physical routes are separate, no CORS is enabled, and no
provider callback, serialized handle or shadow-grant field is accepted.

- `GET /api/physical/status`
- `POST /api/physical/enable` with exactly
  `{ "allow_physical": true, "expected_generation": N }`
- `POST /api/physical/request` with exactly
  `{ "endpoint": "...", "model": "...", "text": "...", "allow_network": true, "expected_generation": N }`
- `POST /api/physical/cancel` with exactly `{ "expected_generation": N }`

The browser first reads the host generation. Enable and request compare that
expected generation atomically with authority state. Enable advances it by one;
request acceptance and command admission do not. Revocation/expiry advance it.
To cancel an in-flight enable based on generation `N`, the panel supplies the
anticipated `N+1`. Cancellation accepts only the current generation or one
anticipated step ahead, then advances past it. Thus a cancellation can fence an
enable in either arrival order, while a delayed old request cannot consume a
new enable. Stale mutations fail closed and require refreshed status and a new
explicit operator action.

Successful JSON has `ok:true`, `mode:"PHYSICAL_EXPERIMENT"`,
`physical_stop_confirmed:false`, provider progress, and a `service` object:
`state`, `reason`, `eligible`, `blockers`, `bundle_digest`, `asset_sha256`,
`generation`, `expires_at_ms`, `request_id`, and `command`. The panel treats
eligibility as a necessary gate, never authority. Host states include disabled,
enabled, pending, leased, expired, cancelled, blocked and closed. Clarification,
unsupported intent and strict-decoder rejection end the request with a terminal
reason rather than authorizing motion.

Generation and request-ID checks are in addition to browser-local epoch fences.
They do not replace the controller's operation identity, immutable bundle
binding, host-monotonic lease, accepted-feedback freshness or final USB dispatch
checks. The HTTP token is an in-memory local request fence, not remote operator
authentication or a persistent credential.
It mitigates browser CSRF and DNS rebinding, not another process on the phone:
a local process can fetch the page and its token. The phone, its applications
and local processes must be trusted; this is not cryptographic operator identity.

## Local HTTP compatibility change

Every state-changing `POST` on the loopback bridge now requires the current
page's `X-Pixel-Shadow-Token`, a loopback `Host`, and matching same-origin browser
headers when present. This includes the existing manual Start, Stand, command,
Stop, reconnect, recording and servo-selection routes; there is no legacy
unauthenticated mutation bypass. Read-only manual status remains available.
Reload the local controller page after a service restart. Custom clients must
obtain the current token from that service's local root page and send the
header; a token from a previous service instance is invalid.

Manual status includes `manual_generation`. HTTP Start and Stand additionally
require `expected_manual_generation` from fresh status. Their host admission
checks and advances that generation atomically with the operation callback.
Stop/reconnect and native controller interruptions invalidate older HTTP
admissions. A delayed old Start/Stand is therefore rejected if an interruption
arrives first; if Start/Stand was already admitted, Stop remains unconditional
and requests its cancellation. These generations belong to the manual HTTP
path and never convey physical AI authority.

The manual page now sends an authenticated `fetch` with `keepalive` for its
best-effort page-exit Stop, rather than `sendBeacon` without a token. It also
cancels an outstanding slider-send timer and sends Stop while Start/Stand is
pending even before a powered status arrives. Restoring a cached page requires
fresh status and never restarts an operation. Browser/OS process death can
prevent any exit callback or request from being delivered; the independent
device watchdog and authorized hardware lifecycle testing remain necessary.
Browser exit delivery is not a physical-stop or torque-off receipt. The
physical panel's generation fencing and independent host expiry remain separate.

## Reproducible UI verification

From the repository root, with Node available:

```sh
node --test pixel_robot/tools/test_physical_ui.cjs
node pixel_robot/tools/test_physical_ui.cjs --output /tmp/physical-ui.json
node --test pixel_robot/tools/test_shadow_ui.cjs
```

The new test reads and executes the **authored production physical-panel
script**, using offline DOM/fetch/timer stubs. It covers explicit enable and
consent, shipped-bundle blocking, single-use submission, URL/text bounds,
malformed/error/timeout paths, repeated clicks, pending/leased cancellation,
manual control and STOP, hidden/navigation/back-forward interruption,
generation/request-ID races, and stale callbacks. It also parses every authored
page script and checks unique element IDs. Physical-panel network routes are
stubbed and restricted to the separate physical API. Additional narrow tests
extract the authored manual HTTP helper, status/button logic and page
exit/restoration callbacks, exercising token-bearing mutations, missing-token
denial, pending starts and the keepalive Stop against in-memory fetch stubs.
The full manual-control script is not executed by this suite.

The JSON report derives test IDs, counts, failures and skipped outcomes from the
actual TAP run, and fingerprints the full HTML, extracted production script and
test source. Use the canonical validation gate for final source-bound evidence;
do not rely on a count copied into prose.

This is **not** visual browser/WebView QA, an Android application build, a live
provider test, USB/hardware execution, physical motion, scheduling-latency
measurement or stop acknowledgement. Those remain separate, explicitly
authorized verification and deployment gates.
