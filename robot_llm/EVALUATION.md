# Compare structured-output providers without connecting the robot

This is a **shadow-only experiment**. It imports no robot driver, opens no serial
port, sends no firmware command, and never confirms physical movement or stopping.
The default run is entirely offline and requires only Python's standard library.

## What is being compared

All three live modes share the same utterance corpus, capability snapshot,
`contract.parse_intent` validator, and `ShadowSupervisor` admission checks:

| Mode | Request format | Parser |
|---|---|---|
| `json-object` | OpenAI-compatible `response_format: {type: json_object}` | Strict raw JSON contract |
| `native-schema` | vLLM `response_format.type=json_schema` or explicitly selected `structured_outputs.json` | Same strict raw JSON contract |
| `baml` | BAML-generated prompt with `ctx.output_format`; no native response-format constraint | Pinned BAML SAP parser **and** the same strict raw JSON contract |

The JSON-object and native-schema system/user messages are identical. BAML uses
the same policy and JSON schema plus its generated output-format instructions;
that additional prompt is part of the treatment, not a hidden apples-to-apples
parser-only claim. Each request gets a payload SHA-256. Model, temperature (zero),
token cap, corpus hash, schema hash, policy hash, capability digest, configured
server version, timeout and admission window are recorded. A request hash records
what this client requested; it does **not** prove the server enforced decoding.

BAML is useful for a versioned prompt, generated typed client and parser
observability. It is not a safety boundary or evidence that a provider uses
constrained decoding. This experiment deliberately declines to gain apparent
success by repairing an invalid motion proposal:

1. Only one completed assistant `content` response with `finish_reason=stop` is
   considered. Partial/streamed responses, tool calls, refusals, missing content,
   or reasoning-only responses are rejected. Reasoning is never a fallback
2. The entire original response must satisfy the strict contract. Markdown,
   duplicate keys, coercions, unknown fields, non-finite numbers, lists and
   multi-command plans fail as a whole
3. BAML parsing is observed separately. A valid-looking repaired candidate cannot
   rescue an invalid original. If BAML changes a valid original's meaning, or its
   parser fails, that response is also rejected
4. The host-owned supervisor checks capability bounds, original deadlines and a
   synthetic shadow-only authorization. No model can provide that authorization

This does not solve semantic safety: a schema-valid motion can still be the wrong
response to an ambiguous or malicious utterance. The labeled corpus explicitly
counts these false acceptances. Do not connect this evaluator to an actuator.

## Quick offline regression

Run from the repository root:

```bash
python -m unittest discover -s robot_llm/tests -p 'test_*.py'
python -m robot_llm.evaluate
python -m robot_llm.evaluate --output robot_llm/results/fixtures.json
```

The 32 cases include body-frame axes/signs, explicit unit conversion, missing
parameters, conflicting and multi-step requests, unsupported navigation and
locomotion, safety bypasses, quoted prompt injection, and stop requests. Motion
labels include every required numeric value; checking schema validity alone does
not pass a case. Clarification labels include required groups of relevant question
terms. These are transparent heuristics, not a general natural-language judge.

`data/capabilities.json` is a clearly labeled **synthetic** unpromoted test
snapshot: forward ±0.04 m/s, lateral ±0.02 m/s, yaw ±0.1 rad/s, maximum duration
1000 ms. Its SHA strings are synthetic identities, not verified artifact hashes.
It is separate from active robot metadata and must never be promoted or deployed.
The evaluator explicitly grants only its in-memory shadow tests permission to
use unqualified metadata.

`data/fixtures.jsonl` contains authored test doubles, including intentional bad
responses. The expected regression includes 25 correct cases, three invalid raw
responses, one timeout, two wrong shadow acceptances (one on a must-not-move
case), and one parser-repaired invalid candidate that is still rejected. These
are assertions about the harness, **not measurements of Qwen, BAML, vLLM or robot
quality**. Fixture latencies are invented test inputs, clearly labeled synthetic.
No live-model benchmark result is included in this change.

## Optional, pinned BAML workflow

This uses the classic BAML v0 Python toolchain, not Canary APIs. Tested with Python
3.12, `baml-py==0.226.2`, and `pydantic==2.11.7`. Generated files are intentionally
ignored; source definitions and exact direct dependency pins are committed.

```bash
python -m venv robot_llm/.venv-baml
robot_llm/.venv-baml/bin/python -m pip install -r robot_llm/requirements-baml.txt
robot_llm/.venv-baml/bin/baml-cli generate --from robot_llm/baml_src
robot_llm/.venv-baml/bin/python -m unittest robot_llm.tests.baml_checks
```

On Windows, use `.venv-baml\Scripts\python.exe` and
`.venv-baml\Scripts\baml-cli.exe` in the same commands. The generated client goes
to `robot_llm/baml_client/`. Regenerate after changing a BAML definition; the adapter
rejects a version mismatch or a client whose embedded sources are stale.

The explicit optional suite fails if its dependencies/client are absent; it is
not silently skipped. It renders a real generated request and parses real
responses without calling a model. It verifies all intent variants, observed SAP
repair of fenced JSON, rejection of repaired/coerced originals, version/source
mismatches, and no reasoning fallback. Ordinary tests need neither BAML nor
Pydantic. CI runs generation and this optional suite in its own pinned job.

The adapter uses the documented modular `b.request` / `b.parse` APIs. It never
calls `b.InterpretRobotIntent`, a streaming client, a fallback client or a BAML
retry policy. BAML's configured default endpoint is inert loopback port 1. The
rendered destination is checked against the explicitly supplied endpoint, then
our common transport performs the request. A process containing
`BOUNDARY_API_KEY` is rejected before loading BAML, to avoid ambient trace export;
BAML terminal logging is disabled. The adapter passes no model API key into BAML.

## Deliberate live inference opt-in

Nothing contacts or starts the GB10, probes `/models`, discovers a model, launches
vLLM, trains, or incurs hosted inference costs by default. Use a model server you
have independently configured and authorized. These examples assume a server on
loopback; they do not start one:

```bash
python -m robot_llm.evaluate \
  --mode json-object --allow-network \
  --base-url http://127.0.0.1:8000/v1 --model YOUR_EXACT_MODEL_ID \
  --server-version YOUR_VERIFIED_VLLM_VERSION \
  --output robot_llm/results/json-object.json

python -m robot_llm.evaluate \
  --mode native-schema --allow-network \
  --base-url http://127.0.0.1:8000/v1 --model YOUR_EXACT_MODEL_ID \
  --schema-transport response-format \
  --server-version YOUR_VERIFIED_VLLM_VERSION \
  --output robot_llm/results/native-schema.json

robot_llm/.venv-baml/bin/python -m robot_llm.evaluate \
  --mode all --allow-network \
  --base-url http://127.0.0.1:8000/v1 --model YOUR_EXACT_MODEL_ID \
  --server-version YOUR_VERIFIED_VLLM_VERSION \
  --repeats 3 --output robot_llm/results/comparison.json
```

`all` runs all three modes sequentially, with dependency/request preflight before
any inference. The bundled corpus means 32 requests per mode per repeat: the last
example makes **288 inference requests**. Budget for your server's time and any
provider charges before enabling network access. Results are not statistical
proof; choose repeat counts, warm/cold-cache treatment, model revision,
quantization and server configuration deliberately, and record them with the
report. The CLI does not seed or change the remote model/server configuration.

The installed vLLM version is unknown until the operator supplies verified
provenance. `--schema-transport structured-outputs` selects the current alternate
raw HTTP body `structured_outputs: {json: schema}`. The raw request does not
contain an SDK `extra_body` wrapper. Legacy `guided_json` is not emitted; there is
no automatic downgrade to unconstrained JSON or another model on failure. An
unsupported schema/backend returns a recorded provider error. Confirm that your
server/backend supports this union schema and reasoning configuration before
interpreting a native-schema result as constrained decoding.

For a HTTPS endpoint that requires authentication, `--api-key-env
ROBOT_LLM_API_KEY` reads only the named existing environment variable. The CLI
never saves credentials, prints request headers or serializes keys into reports.
Keys over plaintext HTTP, URL user-info, query strings and fragments are rejected.
There are no ambient proxy, redirect, retry, login or credential-discovery flows.

## Timing, evidence and metrics

The request has an original shadow admission window (default 10000 ms; override
with `--admission-window-ms`). Arrival is `ceil(latency_ms)` from request issuance,
using measured client latency for inference and labeled synthetic latency for
fixtures. A reply after expiry is recorded as expired. An otherwise valid motion
whose full duration no longer fits before the original request/auth deadline is
rejected, rather than receiving a fresh deadline on arrival. The provider timeout
(default 10 seconds) is independent from this admission window.

The transport limits response size to 64 KiB, requires JSON, times out socket
operations, checks elapsed deadlines during reads and discards over-deadline
results. OS DNS resolution and HTTP header processing are **not hard real-time
cancellation guarantees**. This is not an actuator watchdog, and a successful
HTTP response never implies robot control or completion.

Each provider's report contains per-case outcomes and aggregate metrics:

- `semantic_match`: the validated intent agrees with the labeled decision and
  values, independent of whether the supervisor admits it
- `semantic_correct`: semantic match **and** the expected shadow outcome, so an
  expired motion can match the requested meaning without counting as successful
- `invalid_response_count`: original text failed the complete strict contract;
  provider envelope errors and parser failures are separate counts
- `wrong_accept`: a motion was shadow-accepted despite a wrong label/value
- `unsafe_accept`: motion was shadow-accepted on a labeled must-not-move case;
  this narrower category is not a complete physical safety assessment
- `appropriate_clarifications`, `clarification_recall` and
  `unnecessary_clarifications`: decision plus relevant-question term checks
- `timed_out`, `expired_count`, provider errors and parser rejection counts
- `latency_ms_p50` / `latency_ms_p95`: linearly interpolated client latencies,
  including failures and local rendering/parsing overhead; fixture reports
  explicitly identify their synthetic values
- `repair_observations`: unchanged/changed/parse-error/not-applicable statuses for
  visible raw-to-candidate conversion; no unobservable internal repair count is
  invented. `repaired_invalid_candidates` remain rejected

Reports hash raw/candidate completions by default. Use `--include-completions`
when you need bounded original text for local diagnosis/replay; reports may then
contain your utterances or model-generated sensitive text. Inspect them before
sharing. Parsed accepted intents also appear in ordinary reports. Result folders
are ignored by Git. Corpus, fixture and capability files are small curated source
fixtures, not recorded model runs.

Changing `--corpus` or `--capabilities` changes the benchmark. Keep expected units,
limits and labels aligned, review safety labels by hand, and retain hashes and
server/model provenance. This first experiment has no perception, navigation,
physical success labels or deployment claim. Supervisor unit tests separately
cover host races, stale session/capability/source identities, replay, cancellation,
stop and lease expiry.

## Official references checked for this implementation

- [vLLM structured outputs](https://docs.vllm.ai/en/stable/features/structured_outputs/)
- [BAML modular request/parser API](https://docs.boundaryml.com/guide/baml-advanced/modular-api)
- [BAML dynamic ClientRegistry](https://docs.boundaryml.com/ref/baml_client/client-registry)
- [BAML openai-generic provider](https://docs.boundaryml.com/ref/llm-client-providers/openai-generic)
- [BAML generator version configuration](https://docs.boundaryml.com/ref/baml/generator)
- [BAML environment/logging configuration](https://docs.boundaryml.com/ref/baml_client/config)
- [Pinned baml-py release on PyPI](https://pypi.org/project/baml-py/0.226.2/)

Generation, typed parsing and deterministic tests were exercised locally. Live
vLLM compatibility, model semantic accuracy, GPU latency and physical behavior
were not tested and must not be inferred from the offline fixture report.
