// This schema is a parsing/prompting aid, never an actuator authorization.
// No @assert on containers: never salvage valid parts of a rejected response.
class Clarify {
  schema_version 1
  kind "clarify"
  question string
}

class Unsupported {
  schema_version 1
  kind "unsupported"
  reason string
}

class StopRequest {
  schema_version 1
  kind "stop_request"
}

class VelocityRequest {
  schema_version 1
  kind "velocity_request"
  frame "body"
  forward_m_s float
  lateral_m_s float
  yaw_rate_rad_s float
  duration_ms int
}

// Explicitly inert default. The Python adapter only builds a request here,
// then verifies the caller-supplied URL and sends through its guarded transport.
client<llm> ExplicitEndpoint {
  provider "openai-generic"
  options {
    base_url "http://127.0.0.1:1/v1"
    model "unconfigured-no-network"
    temperature 0
    max_tokens 1024
  }
}

function InterpretRobotIntent(policy: string, utterance: string) -> Clarify | Unsupported | StopRequest | VelocityRequest {
  client ExplicitEndpoint
  prompt #"
    {{ _.role("system") }}
    {{ policy }}
    {{ ctx.output_format }}
    {{ _.role("user") }}
    {{ utterance }}
  "#
}
