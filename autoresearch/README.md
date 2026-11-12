# Robot Training Autoresearch

This is a conservative, sequential ENPIRE-style supervisor for unattended
robot-policy experiments:

```text
Environment -> Policy Improvement -> Rollout -> Evolution
   Isaac             PPO            metrics/video       Qwen
```

The host-side Python controller is intentionally small and deterministic. It
starts and stops only the exact existing Isaac Lab and Qwen containers. Qwen
never receives a Docker socket, shell, SSH key, or unrestricted code-editing
tool. All simulation, rendering, and model inference remain containerized; when
the supervisor finishes or pauses, it stops the container it owns so the GB10
can be used for something else.

The design follows NVIDIA ENPIRE's reset/train/rollout/evolve separation:
<https://research.nvidia.com/labs/gear/enpire/>. It borrows the centralized
lead, shared durable task state, and permission-hook ideas from Claude Code
agent workflows, while using one sequential lead because the GB10 has one GPU
and Isaac/Qwen are intentionally time-sliced:
<https://code.claude.com/docs/en/agent-teams> and
<https://code.claude.com/docs/en/agent-sdk/hooks>.

## User commands

Deploy and start the example Simple Dog job:

```powershell
.\Start-RobotAutoresearch.ps1
```

Run a reviewed set of manifests sequentially:

```powershell
.\Start-RobotAutoresearch.ps1 -Queue "autoresearch\queues\example.json"
```

Validate configuration and workload conflicts without changing containers:

```powershell
.\Start-RobotAutoresearch.ps1 -DryRun
```

Inspect the current phase and evidence location:

```powershell
.\Get-RobotAutoresearchStatus.ps1
```

Request a clean stop:

```powershell
.\Stop-RobotAutoresearch.ps1
```

When a genuine controller or training failure reports `needs_codex`, run a
read-only Codex diagnosis:

```powershell
.\Invoke-RobotAutoresearchCodex.ps1
```

This uses the locally installed `codex exec`, its existing authentication, and
a read-only sandbox. The response is saved both locally and beside the remote
run. Automatic Codex code edits are deliberately disabled until this helper
folder has a reviewed Git/worktree rollback boundary.

Stopping preserves manifests, status, metrics, videos, Qwen decisions,
checkpoints, and Codex escalation requests under:

```text
/home/leo/isaac-workspace/projects/autoresearch
```

## Safety and experiment behavior

- Playback, Onshape streaming, or an externally started Qwen service pauses a
  run instead of being stopped.
- A single file lock prevents two supervisors from running concurrently.
- The controller evaluates the source checkpoint before a continuation and
  promotes a candidate only when the deterministic composite score does not
  regress.
- Promotion compares the starting policy and every candidate using the same
  fixed-command rollout on the selected terrain task. Flat TensorBoard history
  is never used as the baseline for a rough-terrain promotion decision.
- `max_minutes` is accumulated PPO training time. Rendering, evaluation, Qwen
  loading/inference, and other supervisor overhead do not consume it.
- Every run keeps an immutable `best_checkpoint.pth` copy plus provenance in
  `best-checkpoint.json`; rejected candidates cannot overwrite it.
- Qwen may return `continue`, `hold`, or `escalate`. `hold`, `escalate`, invalid
  output, and Qwen request failures are persisted as per-cycle advisories, then
  training continues from the preserved best until the PPO-time budget is met.
- `max_cycles` caps Qwen-guided tuning calls, not PPO duration. After that cap,
  deterministic training, evaluation, and best-checkpoint promotion continue.
- Qwen may change at most two allow-listed numeric parameters per cycle.
- Each numeric change is range checked and limited to 25% from the current
  value.
- Geometry, contacts, termination logic, Docker settings, commands, paths, and
  iteration budgets are not model controlled.
- Genuine training/controller/infrastructure failures or missing visual evidence
  create `codex_request.md` and stop GPU work. Model uncertainty and suspected
  reward exploits do not stop the requested training duration.

## Adding robot designs

Copy `robots/simple-dog.json`, give it a stable `robot_id`, point it at a
validated starting checkpoint, and add its filename to a queue. The controller
executes unique queue entries in order and stops the queue only on a genuine
controller/training failure. The current executable adapter is deliberately limited to
`simple_dog_v1`; a materially different robot needs a reviewed adapter defining:

1. asset/topology validation,
2. training launch and stop behavior,
3. deterministic success and safety metrics,
4. rollout/video generation,
5. checkpoint compatibility and promotion.

This is the boundary that prevents ten heterogeneous robot descriptions from
silently being treated as if they shared the same joints, rewards, or success
criteria.

## Qwen output contract and evidence

The supervisor accepts one complete JSON object from a single finished assistant
`content` string. Markdown/log wrappers, multiple objects, duplicate keys,
non-finite numbers, missing/unknown decision fields, unknown actions, wrong
value types, and more than two tuning proposals are rejected. Reasoning fields,
tool calls, refusals, and truncated completions never supply a decision. Invalid
provider envelopes and request failures use the same advisory path as invalid
model content; they do not shorten the requested PPO duration.

`qwen_decision_schema()` in the deployed supervisor is the structural source of
truth for the prompt, local validator, and optional native schema request. The
local gate also checks the `codex_request`/action relationship and blank text.
`bounded_tuning` remains the authority for safe ranges, maximum per-cycle steps,
and relationships between parameters. Conflicting proposals preserve the prior
tuning and produce an advisory. Only `continue` can apply changes. Confidence and
model claims never decide checkpoint promotion.

Existing manifests keep `qwen_response_format: "json_object"` by default. A
reviewed manifest may explicitly select `"json_schema"` to send:

```python
response_format = {
    "type": "json_schema",
    "json_schema": {"name": "robot_evolution", "schema": qwen_decision_schema()},
}
```

This uses the [documented vLLM response-format API](https://docs.vllm.ai/en/stable/features/structured_outputs/).
The deployed vLLM version and decoding backend must support this schema; they
have not been inferred or upgraded by this change. There is no silent fallback
if a server rejects it. Native schema mode is optional; both modes always pass
through the same local validator and numeric safety gates. Neither JSON mode,
native constrained decoding, nor parsing proves that the model's analysis is
correct. Responses are non-streaming and capped at 1 MiB; decision content is
capped at 64 KiB.

Every requested cycle saves `cycle-N-qwen.json` before sending, then records the
request/validation outcome. It includes the exact prompt text and structural
schema, requested model and generation settings, request SHA-256, current tuning,
metrics and checkpoint references, and the supplied contact-sheet path, byte
count and SHA-256. Only sampled contact-sheet images are sent, not the entire
video. Bounded raw provider output is retained even after rejection; no decision
is taken from stored reasoning. Hashes identify supplied bytes, not the truth or
quality of evidence. `cycle-N-evolution.json` records proposals, actual tuning
before/after, and rejection notes, including cycles after the Qwen call cap.

Run offline contract/provider regressions and existing deterministic gates with:

```sh
python -m unittest discover -s autoresearch -p 'test_*.py' -v
```

These tests mock all model, Docker, and training interactions. They do not claim
compatibility with a live deployment or evaluate Qwen's semantic accuracy.
