# Offline / shadow LLM intents

This package is a **non-actuating experiment** above the robot control loop.
It has no USB, HTTP robot-control, motor, arming, policy-start, firmware or
training integration. `shadow_accepted` means that an offline proposal passed
these checks. It never means that a robot may move, has moved, or is safe.

The stdlib core runs on Python 3.10+. The optional provider experiment compares
complete model responses through the same independent validator; see
[provider evaluation](EVALUATION.md) for that workflow and its limitations.

## Run and validate the integrated software

- [Interactive shadow session](SESSION.md): manual proposals or explicitly
  configured live inference, with responsive cancellation and no robot transport
- [Retained-report qualification](QUALIFICATION.md): independently replay model
  evidence against reviewed criteria; synthetic reports cannot qualify a model
- [Authored evaluation corpus](QUALIFICATION-CORPUS.md): 120 labeled instructions,
  with no claim of measured model accuracy or independently verified holdout
- [Canonical validation loop](../validation/README.md): after isolated setup,
  `python -m validation` runs the offline plan; bounded watch mode reruns after
  fixes and never edits code or starts inference itself

## Contract

`contract.py` provides frozen dataclasses, `parse_intent(raw)`,
`intent_to_dict(intent)` and `intent_schema()`. The complete response is exactly
one object with `schema_version: 1` and one of these variants:

| `kind` | Additional required fields |
| --- | --- |
| `clarify` | `question`: nonblank string, at most 500 characters |
| `unsupported` | `reason`: nonblank string, at most 500 characters |
| `stop_request` | none |
| `velocity_request` | `frame: "body"`, `forward_m_s`, `lateral_m_s`, `yaw_rate_rad_s`, `duration_ms` |

Velocity values are finite JSON numbers with explicit SI units in their field
names. The shadow convention is forward-positive, left-positive lateral and
counterclockwise-positive yaw. Any future runtime adapter must verify those
axes against its actual frame contract. Duration is a JSON integer from 1 to 5,000 ms, additionally limited by
the host's capability snapshot and remaining original deadline. Decimal
representations such as `500.0` are intentionally rejected for integer fields,
even though some JSON Schema implementations consider them integers.

Unknown/missing fields, duplicate keys (including escaped-key duplicates),
NaN/infinity, booleans in numeric fields, numeric strings, invalid Unicode
surrogates, unsupported frames,
fences, reasoning text, concatenated objects, plans and partial objects all
fail closed. Responses are bounded to 16,384 characters. No valid-looking
substring is extracted and no invalid step is dropped. A raw parse failure is
not repaired at this boundary. Schema-constrained generation does not remove
the need to run this validator.

The contract excludes servo values, arming, calibration, policy selection,
posture, arbitrary code, shell commands, navigation and named destinations.
Requested velocity times requested duration is **not measured displacement**.
The language model cannot establish ground truth, obstacle clearance,
localization, task success or authorization. Scene descriptions and camera
text remain untrusted observations, even when they contain operator-like
instructions.

## Trusted host boundary

The host selects the active bundle metadata; it must not take that metadata,
the envelope, the clock or authorization from a model response. Use
`CapabilitySnapshot.from_metadata_json()` to retain strict duplicate-key
checking when loading metadata bytes. Only the currently supported metadata
schema v2 and all three explicit velocity limits are accepted. Snapshot
identity binds the **entire** metadata object and the configured duration
limit. No bounds are taken from README defaults or a standalone goal helper.

The checked-in Pixel bundle is currently a restricted, unpromoted test
candidate and says `physical_transport_verified: false`. Its declared bounds
are ±0.04 m/s forward, ±0.02 m/s lateral and ±0.1 rad/s yaw. These flags and
hashes are metadata claims: this package does not verify actor bytes,
promotion evidence, calibration, transport health or actual deployment.
A metadata digest is an identity check, not authentication or a safety proof.

Every velocity proposal requires a host-created `ShadowAuthorization`, bound
to session, source, capability digest and expiry. Metadata without both
promotion and transport-verification claims additionally requires
`allow_unqualified_metadata=True`, explicitly granting an **offline test**.
That switch must never be interpreted as a live candidate allowlist or robot
permission. Even metadata that claims both properties still needs a grant.
The default 1,000 ms shadow duration and absolute 5,000 ms schema ceiling are
experiment limits, not physically validated stopping limits.

`ShadowSupervisor.begin_request()` records a host envelope *before* inference:
unique request ID, session/source generation, issue time, original expiry and
capability digest. Choose a new, unique session ID for each supervisor session.
All event times are milliseconds from one host monotonic clock, never
model-provided timestamps. Submit the original envelope unchanged.
Retries cannot extend it. Any final submission resolves that request; rejected
IDs cannot be reused. A provider retry before submission must still finish
within the original envelope and cannot retry a physical action.

`submit()` performs deterministic whole-object validation, deadline and
identity checks, authorization checks and active capability bounds checks.
It rejects rather than clamps values. The whole requested duration must fit
both the original request expiry and grant expiry. At most one request owns a
shadow lease at a time, across all sources; new requests do not silently
preempt it. Starting a newer request automatically cancels older **pending**
proposals from that same source, preventing delayed replies from becoming an
implicit command queue. It does not cancel an already admitted shadow lease;
use `cancel_source()` explicitly before replacing that active request. A new
proposal submitted while a lease remains active is rejected.

Cancellation and submission are serialized under a lock. Cancelling a source
advances its generation and cancels its pending and shadow-active requests.
Changing the capability digest cancels all such requests. Grants with a
different digest cannot authorize new requests. Grants are digest-scoped, not
a revocation registry: reverting from capability A to B and back to A can
reuse an unexpired grant for a newly issued request. Cancelled envelopes never
revive. Use a new unique session identity when revoking all existing grants.
Terminal outcomes and request IDs remain in memory to prevent replay;
use short-lived supervisors per experiment/session rather than a long-lived
server. There is no persistent recovery or authorization service here.

## Deterministic stop and truthful outcomes

A trusted stop button/event calls `request_stop(now_ms=...)` **directly**, with
no LLM call or parsing dependency. It cancels every pending/shadow-active
request and latches the supervisor. A new shadow session must be constructed
explicitly to accept later requests. An on-time model `stop_request` uses that
same cancellation path, but is not a substitute for the trusted stop input.

Stop returns `stop_requested`, always with `physical_stop_confirmed=False`.
Nothing in this package sends a physical stop, selects hold versus torque-off,
or observes hardware acknowledgement. A real integration needs its existing
independent stop path and separately verified fault-specific stop semantics.

Other states are `shadow_accepted`, `clarify`, `unsupported`, `rejected`,
`cancelled`, `expired` and `unknown`. `expire()` / `outcome()` update recorded
leases when polled. Expiry is not completion and never produces `succeeded`.
There is no background timer controlling a robot. Any future live executor
needs its own bounded timer/watchdog, state/arming/sensor-freshness checks,
transport integrity and measured completion predicates independent of model
availability. An accepted request, HTTP 200 or elapsed timer cannot prove
arrival or standing stability.

## CPU-only example

For an executable test through the actual bundled Pixel actor and Kotlin
runtime math, see [RUNTIME-BRIDGE.md](RUNTIME-BRIDGE.md). That path is strictly
compute-only: authored proposals and synthetic feedback produce diagnostic
values, never robot authorization or hardware writes.

Run from the repository root. This only reads local metadata and evaluates an
offline JSON fixture:

```python
from pathlib import Path
from robot_llm import CapabilitySnapshot, ShadowAuthorization, ShadowSupervisor

caps = CapabilitySnapshot.from_metadata_json(
    Path("pixel_robot/app/src/main/assets/policy_metadata.json").read_text(),
    max_duration_ms=1_000,
)
supervisor = ShadowSupervisor(session_id="offline-demo", capabilities=caps)
envelope = supervisor.begin_request(
    source_id="operator-fixture", request_id="request-1", now_ms=0, ttl_ms=1_000,
)
grant = ShadowAuthorization(
    session_id="offline-demo", source_id="operator-fixture",
    capability_digest=caps.digest, expires_at_ms=1_000,
    allow_unqualified_metadata=True,  # Explicit test-only grant for this fixture
)
raw = '''{"schema_version":1,"kind":"velocity_request","frame":"body",
"forward_m_s":0.04,"lateral_m_s":0,"yaw_rate_rad_s":0,"duration_ms":500}'''
result = supervisor.submit(envelope, raw, now_ms=1, authorization=grant)
assert result.state.value == "shadow_accepted"
assert supervisor.outcome("request-1", now_ms=501).state.value == "expired"
assert supervisor.request_stop(now_ms=502).physical_stop_confirmed is False
```

Run deterministic tests without a model, API key, BAML runtime or GPU:

```sh
python -m unittest discover -s robot_llm/tests -p 'test_*.py'
```

Core coverage includes malicious/malformed/truncated responses, strict units,
unknown/duplicate keys, candidate restrictions, replay, expired and forged
host envelopes, changed capabilities, source arbitration, cancellation before
and after replies, and concurrent cancellation/submission. A finite test
corpus establishes regression evidence only, never physical safety or model
semantic reliability. Native decoding and BAML must be measured separately
on raw schema validity, parser repair, semantic accuracy and false shadow
admissions; raw type validity alone is insufficient.

## Android shadow preview

The existing Pixel control page now has a service-owned, shadow-only text/provider
flow. See [SHADOW-PREVIEW.md](../pixel_robot/docs/SHADOW-PREVIEW.md) for setup,
HTTPS/consent requirements and tests. Intent previews never authorize actuation;
full Android/device and real-provider acceptance remain separate.
