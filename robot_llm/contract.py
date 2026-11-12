"""Small, strict wire contract for non-actuating language-model proposals.

The model owns only the intent. Session identity, authorization, capability
identity and deadlines are supplied independently by the trusted host.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
from typing import Literal, Union

SCHEMA_VERSION = 1
MAX_DURATION_MS = 5_000
MAX_RESPONSE_CHARS = 16_384
MAX_TEXT_CHARS = 500


class ContractError(ValueError):
    """The complete response does not conform to the intent contract."""


def _valid_unicode(value: str, name: str) -> None:
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeEncodeError as exc:
        raise ContractError(f"{name} contains an unpaired Unicode surrogate") from exc


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        _valid_unicode(key, "object key")
        if key in result:
            raise ContractError(f"duplicate key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ContractError(f"non-finite JSON constant: {value}")


def strict_json_object(raw: str) -> dict[str, object]:
    """Read exactly one object without repair, key loss or numeric coercion."""
    if type(raw) is not str or not raw or len(raw) > MAX_RESPONSE_CHARS:
        raise ContractError("response must be a nonempty bounded JSON string")
    _valid_unicode(raw, "response")
    try:
        value = json.loads(raw, object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
    except (ValueError, RecursionError) as exc:
        raise ContractError(f"invalid complete JSON object: {exc}") from exc
    if type(value) is not dict:
        raise ContractError("response must be one JSON object")
    return value


def finite_number(value: object, name: str) -> float:
    """Reject booleans, strings, overflow and non-finite numbers."""
    if type(value) not in (int, float):
        raise ContractError(f"{name} must be a finite number, not a coerced value")
    try:
        number = float(value)
    except (OverflowError, ValueError) as exc:
        raise ContractError(f"{name} must be finite") from exc
    if not math.isfinite(number):
        raise ContractError(f"{name} must be finite")
    return number


def integer(value: object, name: str, *, minimum: int = 0,
            maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ContractError(f"{name} must be an integer in the permitted range")
    return value


def text_field(value: object, name: str, *, maximum: int = MAX_TEXT_CHARS) -> str:
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise ContractError(f"{name} must be nonblank text of at most {maximum} characters")
    _valid_unicode(value, name)
    return value


@dataclass(frozen=True)
class Clarify:
    question: str
    schema_version: Literal[1] = field(default=SCHEMA_VERSION, init=False)
    kind: Literal["clarify"] = field(default="clarify", init=False)


@dataclass(frozen=True)
class Unsupported:
    reason: str
    schema_version: Literal[1] = field(default=SCHEMA_VERSION, init=False)
    kind: Literal["unsupported"] = field(default="unsupported", init=False)


@dataclass(frozen=True)
class StopRequest:
    schema_version: Literal[1] = field(default=SCHEMA_VERSION, init=False)
    kind: Literal["stop_request"] = field(default="stop_request", init=False)


@dataclass(frozen=True)
class VelocityRequest:
    forward_m_s: float
    lateral_m_s: float
    yaw_rate_rad_s: float
    duration_ms: int
    schema_version: Literal[1] = field(default=SCHEMA_VERSION, init=False)
    kind: Literal["velocity_request"] = field(default="velocity_request", init=False)
    frame: Literal["body"] = field(default="body", init=False)


Intent = Union[Clarify, Unsupported, StopRequest, VelocityRequest]


def intent_schema() -> dict[str, object]:
    """Return a fresh schema for full-response constrained generation.

    Capability-specific limits are deliberately a separate host-side check.
    JSON Schema cannot express duplicate-key rejection or finite IEEE values;
    parse_intent remains mandatory even with constrained generation.
    """
    def variant(kind: str, fields: dict[str, object]) -> dict[str, object]:
        properties = {"schema_version": {"type": "integer", "const": SCHEMA_VERSION},
                      "kind": {"type": "string", "const": kind}, **fields}
        return {"type": "object", "properties": properties,
                "required": list(properties), "additionalProperties": False}

    bounded_text = {"type": "string", "minLength": 1, "maxLength": MAX_TEXT_CHARS,
                    "pattern": r"\S"}
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "ShadowIntentV1",
        "oneOf": [
            variant("clarify", {"question": dict(bounded_text)}),
            variant("unsupported", {"reason": dict(bounded_text)}),
            variant("stop_request", {}),
            variant("velocity_request", {
                "frame": {"type": "string", "const": "body"},
                "forward_m_s": {"type": "number"},
                "lateral_m_s": {"type": "number"},
                "yaw_rate_rad_s": {"type": "number"},
                "duration_ms": {"type": "integer", "minimum": 1,
                                "maximum": MAX_DURATION_MS},
            }),
        ],
    }


def parse_intent(raw: str) -> Intent:
    value = strict_json_object(raw)
    if type(value.get("schema_version")) is not int or value["schema_version"] != SCHEMA_VERSION:
        raise ContractError("unsupported schema_version")
    kind = value.get("kind")
    variants = {schema["properties"]["kind"]["const"]: schema
                for schema in intent_schema()["oneOf"]}
    if type(kind) is not str or kind not in variants:
        raise ContractError("unsupported intent kind")
    if set(value) != set(variants[kind]["required"]):
        raise ContractError("intent contains unknown or missing fields")
    if kind == "clarify":
        return Clarify(text_field(value["question"], "question"))
    if kind == "unsupported":
        return Unsupported(text_field(value["reason"], "reason"))
    if kind == "stop_request":
        return StopRequest()
    if value["frame"] != "body" or type(value["frame"]) is not str:
        raise ContractError("only the body frame is supported")
    return VelocityRequest(
        forward_m_s=finite_number(value["forward_m_s"], "forward_m_s"),
        lateral_m_s=finite_number(value["lateral_m_s"], "lateral_m_s"),
        yaw_rate_rad_s=finite_number(value["yaw_rate_rad_s"], "yaw_rate_rad_s"),
        duration_ms=integer(value["duration_ms"], "duration_ms", minimum=1,
                            maximum=MAX_DURATION_MS),
    )


def intent_to_dict(intent: Intent) -> dict[str, object]:
    """Serialize and revalidate, including directly constructed dataclasses."""
    if type(intent) not in (Clarify, Unsupported, StopRequest, VelocityRequest):
        raise ContractError("unknown intent type")
    result = asdict(intent)
    try:
        parse_intent(json.dumps(result, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise ContractError(f"invalid intent: {exc}") from exc
    return result
