# Interactive LLM shadow sessions

`python -m robot_llm.session` is a usable JSONL session above the existing strict
contract, provider adapters and shadow supervisor. It never imports a robot
runtime, opens a robot transport, arms anything, moves hardware, or claims a
physical outcome. It does not start or discover GB10/vLLM services.

A natural-language request can now go through an explicitly configured model,
using the selected capability snapshot, a host-issued request envelope and a
host-issued shadow authorization, to a structured outcome. The offline mode
accepts **authored JSON proposals**, not natural language. It is explicitly
labeled `manual_proposal_not_model_inference`; it is not a language model or a
model-quality demonstration.

## Run the offline session first

From the repository root, Python 3.10+ on Linux/macOS:

```bash
python -m robot_llm.session \
  --mode manual --allow-shadow --allow-unqualified-metadata \
  --capabilities pixel_robot/app/src/main/assets/policy_metadata.json
```

The selected checked-in metadata is currently an unpromoted, transport-unverified
candidate. `--allow-unqualified-metadata` is therefore needed to admit velocity
**shadow tests**. Without it, velocity proposals are rejected. This flag does not
promote the bundle or grant any physical permission. `--allow-shadow` is required
for every session, even with metadata claiming promotion and verified transport.

Paste one complete JSON object per line, pressing Enter after each:

```json
{"op":"propose","raw":"{\"schema_version\":1,\"kind\":\"velocity_request\",\"frame\":\"body\",\"forward_m_s\":0.02,\"lateral_m_s\":0,\"yaw_rate_rad_s\":0,\"duration_ms\":100}"}
{"op":"status"}
{"op":"stop"}
{"op":"close"}
```

`raw` is intentionally a string, preserving the original proposal bytes and
repeated keys for strict validation. Do not turn it into an already-decoded
object. No valid-looking substring or partial completion is extracted.

The velocity example produces `shadow_accepted` if it passes all checks, then
`expired` after its 100 ms shadow lease. Neither means the robot moved. A stop
produces `stop_requested` with `physical_stop_confirmed:false`. It only cancels
this in-memory experiment; use the robot's existing independent stop controls
for real hardware.

For a deterministic pipe smoke test with no model, network, BAML or GPU:

```bash
printf '%s\n' \
  '{"op":"propose","raw":"{\"schema_version\":1,\"kind\":\"clarify\",\"question\":\"Which speed and duration?\"}"}' \
  '{"op":"close"}' \
  | python -m robot_llm.session --mode manual --allow-shadow \
      --capabilities pixel_robot/app/src/main/assets/policy_metadata.json
```

The core is portable stdlib Python. The CLI additionally requires nonblocking
stdin/stdout descriptor support. Linux pipe/terminal behavior is tested here.
On Windows, use WSL for these commands; native Windows console behavior is not
validated. Python 3.12+ adds Windows pipe support, but unsupported descriptors
fail at startup with `nonblocking_stdio_unavailable` instead of falling back to
blocking I/O. The existing evaluator remains an alternative for offline batch
work. This change makes no claim of native Windows interactive verification.

## Explicit model opt-in

Only use an independently configured and authorized inference endpoint. Replace
both placeholders below; this command neither discovers a destination/model nor
starts a server. The endpoint receives the operator text, the intent instructions
and the limited capability context (bounds, duration and capability digest).
Each request can consume inference resources or incur the provider's charges.

```bash
python -m robot_llm.session \
  --mode native-schema --allow-network --allow-shadow \
  --allow-unqualified-metadata \
  --base-url https://YOUR_AUTHORIZED_MODEL_HOST/v1 \
  --model YOUR_EXACT_MODEL_ID \
  --capabilities pixel_robot/app/src/main/assets/policy_metadata.json \
  --request-ttl-ms 10000 --timeout-seconds 8
```

Then, while stdin remains open, send:

```json
{"op":"request","text":"In the body frame, move forward at 0.02 m/s for 0.1 seconds."}
{"op":"cancel"}
{"op":"request","text":"Turn left."}
{"op":"stop"}
{"op":"close"}
```

Wait for the first outcome if you want to inspect it; sending `cancel` immediately
is intentionally a cancellation test. `Turn left` lacks speed and duration and
should yield a clarification, but model semantics are not guaranteed. A
schema-valid wrong answer remains possible and is why this stays non-actuating.

Other provider modes are `json-object` and `baml`. Native-schema requests can use
`--schema-transport structured-outputs` when that is the operator-verified server
contract. No automatic downgrade, fallback, redirect or retry exists. BAML
requires the pinned dependency installation and generated client described in
[EVALUATION.md](EVALUATION.md). It is not imported in manual/default mode; no
BAML rendering or inference happens until an explicit BAML request. BAML trace
export remains disabled/rejected by the provider's existing isolation checks.

For HTTPS authentication, `--api-key-env ROBOT_LLM_API_KEY` reads only that named
existing environment variable. The session does not find, save or print keys.
No endpoint URL, model identifier, provider exception, request text, response
body or headers are logged. Parsed intent text, including a model's clarification
or unsupported reason, is included in outcome events and remains untrusted
model output. Review any event log before sharing it. API keys over plaintext
HTTP and credentials/query strings/fragments in endpoint URLs are rejected.

## JSONL protocol and outcomes

The input channel is a **trusted operator channel**. Do not connect model,
camera, website or other untrusted output to stdin as session operations. Model
output goes only through the provider-result and strict-intent path.

| Operation | Exact input fields | Meaning |
| --- | --- | --- |
| `request` | `op`, `text` | Natural-language inference, live modes only; text is nonblank and at most 8,000 characters |
| `propose` | `op`, `raw` | Authored complete intent, manual mode only |
| `cancel` | `op` | Cancel pending and admitted shadow work; allow a later new request |
| `stop` | `op` | Direct trusted stop; cancel and latch this session without any model call |
| `status` | `op` | Poll expiry and report current pending request/digest/session state |
| `close` | `op` | Stop, cancel, reap local work and exit |

Duplicate/unknown keys, invalid values, malformed JSON and unsupported operations
are rejected as a whole. A malformed command cancels pending/active shadow work
and emits a generic `command_error`, without echoing the input. The session can
then accept a fresh valid request unless stop is latched. Oversized input closes
the session. The whole JSONL command is limited to 16,384 characters, including
JSON escaping and any line terminator. The inner intent has the existing strict
16,384-character contract limit as well.

Every output line is an event with `event_version:1`, increasing `sequence`, a
fresh session UUID, the host's monotonic milliseconds, `mode:"SHADOW"`, provider
mode, `physical_execution:false` and `physical_stop_confirmed:false`. Request
IDs are generated by the host as `r1`, `r2`, etc. They cannot be supplied by the
model or reused. `request_started` includes the original complete envelope.
`outcome` includes its request ID, state, reason, parsed intent when available,
and shadow lease expiry when applicable.

Outcomes are the supervisor's existing `shadow_accepted`, `clarify`,
`unsupported`, `stop_requested`, `rejected`, `cancelled` and `expired` states.
Provider/start/parser errors generate a sanitized `provider_error`; the related
request is cancelled or remains expired, never retried. Invalid raw responses
are rejected by the supervisor even if BAML returns a repaired candidate. A
changed meaning in a valid raw response is cancelled with a parser error.

There are no stdout banners or raw provider diagnostics. Native and Python
stdout/stderr are suppressed inside the disposable inference process. Ordinary
startup configuration errors use fixed diagnostic codes on stderr and exit 2.
Normal close/EOF exits 0; Ctrl-C exits 130; fatal I/O/monitor errors exit 2.

## Timing, cancellation and lifecycle

- The host uses `time.monotonic_ns() // 1_000_000`. Reported provider latency,
  fixture latency and model-provided times never establish an admission deadline
- The envelope and digest-scoped authorization are created **before** provider
  work. The default 10,000 ms request lifetime includes process startup, network,
  inference, parsing, IPC and cleanup. `--request-ttl-ms` is limited to 1–60,000
- The entire requested duration must fit the original request and authorization
  expiry. `--max-duration-ms` defaults to 1,000 and cannot exceed the core's
  5,000 ms ceiling. These are experiment limits, not verified stopping times
- Latest-request-wins is explicit: issuing a new request cancels the old pending
  proposal **and any already admitted shadow lease** before replacement. There
  is no model-action queue or silent physical preemption
- A trusted stop is latched and recorded before provider cleanup. An on-time
  complete model stop uses the same latch, but it is not the trusted stop button.
  Starting again requires constructing a fresh session explicitly
- At most one provider process exists. It is terminated/reaped on cancellation,
  replacement, expiry, stop, EOF, Ctrl-C or close. A 20 ms host monitor polls
  completion and lease expiry while input is idle. This is not a hard-real-time
  watchdog. Process termination cannot retract an already-received remote
  inference request or guarantee the remote server has stopped computing
- No local provider process or monitor thread remains after CLI shutdown. Stop
  and close never claim that real robot motors stopped
- Default session history is bounded at 1,000 issued requests; configurable
  `--max-requests` is limited to 1–10,000. A fresh session is required at the cap
- Capability files are read once at startup, not watched. The caller must select
  current metadata; there is no assertion about the deployed robot's actual
  bundle. The programmatic `replace_capabilities(snapshot)` method cancels stale
  pending/active work and subsequent requests use the new digest and bounds

**Keep stdin open and continuously drain stdout during live work.** EOF cancels
pending inference immediately; `echo '{"op":"request",...}' | ...` will usually
cancel before any reply. EOF is deliberately not an implicit “wait for model”
command. A client should wait for a terminal outcome, send close, then close its
pipe. A accepted shadow lease may later emit expired or cancelled.

The CLI uses bounded nonblocking UTF-8 I/O so an idle reader can be interrupted
by a fatal monitor error, and an unread output pipe cannot hold the session lock
and delay stop indefinitely. Output backpressure fails closed and reaps work.
A partial final line is possible if the OS cannot accept a complete event;
consumers must discard incomplete lines and treat process loss as cancellation,
not success. Events are not a durable audit log. If embedding `run_jsonl` with
custom streams, supply a nonblocking reader with `cancel_read()` or arrange for
EOF after failure; an arbitrary blocking custom `readline()` cannot be woken by
Python. Event callbacks in the Python API must be fast/nonblocking.

## Local tests and next Codex loop

```bash
python -m unittest robot_llm.tests.test_session -v
python -m unittest discover -s robot_llm/tests -p 'test_*.py'
python -m robot_llm.session --help
```

Tests inject clocks, pending providers and an actual provider adapter with a
mocked transport. They cover host deadlines independent of claimed latency,
remaining-duration rejection, expiry while idle, source replacement, capability
replacement, malformed/partial output, BAML-equivalence gating, manual labeling,
explicit grants, races between stop and completion, EOF/Ctrl-C, process-start
interruption, native log suppression, output backpressure and local process/
monitor cleanup. Spawned-process tests only execute local test functions; no
endpoint is called and no socket is bound.

Before considering a future integration, Codex/operator follow-up should:

1. Run the full offline suite on the final combined checkout and exercise the
   intended client's nonblocking I/O/stop/EOF/error behavior
2. Verify the exact already-running endpoint, model, server version and decoding
   support independently; obtain authorization before any live inference run
3. Install/regenerate pinned BAML only if testing BAML, and run its separate
   offline suite before requesting live completions
4. Use a deliberately bounded live shadow session and the labeled evaluation
   corpus; preserve honest distinctions between unit-test doubles, real model
   responses, semantic correctness and physical evidence
5. Keep hardware integration out of this session. Any future actuator adapter
   needs separate reviewed authorization, independent stops/watchdogs and actual
   transport/sensor/completion evidence

No live model, GB10 workload, Isaac run or physical behavior was tested as part
of this session implementation.
