"""Offline/shadow LLM intent experiments; no robot-control transport."""
from .contract import (Clarify, ContractError, Intent, StopRequest, Unsupported,
                       VelocityRequest, intent_schema, intent_to_dict, parse_intent)
from .supervisor import (CapabilitySnapshot, HostEnvelope, Outcome, OutcomeState,
                         ShadowAuthorization, ShadowSupervisor)

__all__ = ["Clarify", "ContractError", "Intent", "StopRequest", "Unsupported",
           "VelocityRequest", "intent_schema", "intent_to_dict", "parse_intent",
           "CapabilitySnapshot", "HostEnvelope", "Outcome", "OutcomeState",
           "ShadowAuthorization", "ShadowSupervisor"]
