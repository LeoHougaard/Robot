"""Deterministic, in-memory SHADOW admission. There is no motion transport.

This module cannot attest to robot health, arming, perception, obstacle
clearance or physical completion. Its states refer only to offline proposals.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import json
import re
import threading
from typing import Literal

from .contract import (Clarify, ContractError, Intent, MAX_DURATION_MS,
                       StopRequest, Unsupported, VelocityRequest, finite_number,
                       integer, parse_intent, strict_json_object, text_field)

_AXES = ("forward_m_s", "lateral_m_s", "yaw_rate_rad_s")


def _digest(value: object) -> str:
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError) as exc:
        raise ContractError("metadata must be finite, JSON-serializable data") from exc
    return hashlib.sha256(encoded).hexdigest()


def _sha(value: object, name: str) -> str:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ContractError(f"{name} must be a lowercase SHA-256 digest")
    return value


@dataclass(frozen=True)
class CapabilitySnapshot:
    """Limits and metadata claims from the bundle selected by the host.

    Digest binds the entire supplied metadata and the shadow duration limit.
    It does not verify asset bytes, promotion evidence or physical transport.
    """
    metadata_digest: str
    profile_sha256: str
    checkpoint_sha256: str
    weights_sha256: str
    deployment_tier: str
    promoted: bool
    physical_transport_verified: bool
    forward_m_s: tuple[float, float]
    lateral_m_s: tuple[float, float]
    yaw_rate_rad_s: tuple[float, float]
    max_duration_ms: int = 1_000

    def __post_init__(self) -> None:
        for name in ("metadata_digest", "profile_sha256", "checkpoint_sha256", "weights_sha256"):
            _sha(getattr(self, name), name)
        text_field(self.deployment_tier, "deployment_tier")
        if type(self.promoted) is not bool or type(self.physical_transport_verified) is not bool:
            raise ContractError("qualification metadata must contain literal booleans")
        for axis in _AXES:
            bounds = getattr(self, axis)
            if type(bounds) is not tuple or len(bounds) != 2:
                raise ContractError(f"{axis} must have immutable lower/upper bounds")
            low, high = (finite_number(v, axis) for v in bounds)
            if low > 0 or high < 0 or low > high:
                raise ContractError(f"{axis} bounds must be ordered and include zero")
        integer(self.max_duration_ms, "max_duration_ms", minimum=1, maximum=MAX_DURATION_MS)

    @classmethod
    def from_metadata(cls, metadata: dict[str, object], *, max_duration_ms: int = 1_000) -> "CapabilitySnapshot":
        if type(metadata) is not dict:
            raise ContractError("metadata must be an object")
        if type(metadata.get("schema_version")) is not int or metadata["schema_version"] != 2:
            raise ContractError("unsupported active metadata schema_version")
        limits = metadata.get("validated_command_limits")
        if type(limits) is not dict or set(limits) != set(_AXES):
            raise ContractError("metadata must provide exactly the three velocity-axis limits")
        bounds = {}
        for axis in _AXES:
            values = limits[axis]
            if type(values) is not list or len(values) != 2:
                raise ContractError(f"invalid metadata bounds for {axis}")
            bounds[axis] = tuple(finite_number(v, axis) for v in values)
        try:
            return cls(metadata_digest=_digest(metadata),
                       profile_sha256=metadata["profile_sha256"],
                       checkpoint_sha256=metadata["checkpoint_sha256"],
                       weights_sha256=metadata["weights_sha256"],
                       deployment_tier=metadata["deployment_tier"],
                       promoted=metadata["promoted"],
                       physical_transport_verified=metadata["physical_transport_verified"],
                       max_duration_ms=max_duration_ms, **bounds)
        except KeyError as exc:
            raise ContractError(f"missing active-bundle metadata: {exc.args[0]}") from exc

    @classmethod
    def from_metadata_json(cls, raw: str, *, max_duration_ms: int = 1_000) -> "CapabilitySnapshot":
        return cls.from_metadata(strict_json_object(raw), max_duration_ms=max_duration_ms)

    @property
    def digest(self) -> str:
        return _digest({name: getattr(self, name) for name in self.__dataclass_fields__})

    @property
    def requires_unqualified_test_opt_in(self) -> bool:
        return not (self.promoted and self.physical_transport_verified)


@dataclass(frozen=True)
class HostEnvelope:
    session_id: str
    session_generation: int
    source_id: str
    source_generation: int
    request_id: str
    issued_at_ms: int
    expires_at_ms: int
    capability_digest: str

    def __post_init__(self) -> None:
        for name in ("session_id", "source_id", "request_id"):
            text_field(getattr(self, name), name)
        for name in ("session_generation", "source_generation", "issued_at_ms", "expires_at_ms"):
            integer(getattr(self, name), name)
        if self.expires_at_ms <= self.issued_at_ms:
            raise ContractError("request expiry must follow its original issue time")
        _sha(self.capability_digest, "capability_digest")


@dataclass(frozen=True)
class ShadowAuthorization:
    """Explicit host grant for this offline test, NEVER a physical permission."""
    session_id: str
    source_id: str
    capability_digest: str
    expires_at_ms: int
    allow_unqualified_metadata: bool = False

    def __post_init__(self) -> None:
        text_field(self.session_id, "session_id")
        text_field(self.source_id, "source_id")
        _sha(self.capability_digest, "capability_digest")
        integer(self.expires_at_ms, "expires_at_ms")
        if type(self.allow_unqualified_metadata) is not bool:
            raise ContractError("allow_unqualified_metadata must be a literal boolean")


class OutcomeState(str, Enum):
    SHADOW_ACCEPTED = "shadow_accepted"
    CLARIFY = "clarify"
    UNSUPPORTED = "unsupported"
    STOP_REQUESTED = "stop_requested"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Outcome:
    state: OutcomeState
    reason: str
    envelope: HostEnvelope | None = None
    intent: Intent | None = None
    lease_expires_at_ms: int | None = None
    physical_stop_confirmed: Literal[False] = field(default=False, init=False)


class ShadowSupervisor:
    """Serialize host events, fence stale model replies and record shadow leases.

    All times are caller-supplied milliseconds on ONE monotonic host clock.
    Poll expire() to update recorded leases; no background task or I/O exists.
    At most one velocity proposal is shadow-active across all sources.
    """

    def __init__(self, *, session_id: str, capabilities: CapabilitySnapshot):
        self._session_id = text_field(session_id, "session_id")
        if type(capabilities) is not CapabilitySnapshot:
            raise ContractError("capabilities must be a CapabilitySnapshot")
        self._capabilities = capabilities
        self._session_generation = 0
        self._source_generations: dict[str, int] = {}
        self._requests: dict[str, HostEnvelope] = {}
        self._outcomes: dict[str, Outcome] = {}
        self._stop_latched = False
        self._last_now_ms = 0
        self._lock = threading.RLock()

    @property
    def capabilities(self) -> CapabilitySnapshot:
        return self._capabilities

    def _clock(self, now_ms: int) -> None:
        integer(now_ms, "now_ms")
        if now_ms < self._last_now_ms:
            raise ContractError("host monotonic clock moved backwards")
        self._last_now_ms = now_ms

    def _record(self, envelope: HostEnvelope, state: OutcomeState, reason: str,
                intent: Intent | None = None, lease: int | None = None) -> Outcome:
        result = Outcome(state, reason, envelope, intent, lease)
        self._outcomes[envelope.request_id] = result
        return result

    def begin_request(self, *, source_id: str, request_id: str,
                      now_ms: int, ttl_ms: int) -> HostEnvelope:
        with self._lock:
            self._clock(now_ms)
            text_field(source_id, "source_id")
            text_field(request_id, "request_id")
            integer(ttl_ms, "ttl_ms", minimum=1, maximum=60_000)
            if request_id in self._requests:
                raise ContractError("request_id already used in this supervisor")
            if self._stop_latched:
                raise ContractError("stop is latched; create a new shadow session explicitly")
            # This is a single-intent interface, not an implicit command queue.
            # A newer utterance fences older pending proposals from that source;
            # an already admitted lease still requires explicit cancellation.
            for old_id, old in self._requests.items():
                if old.source_id == source_id and old_id not in self._outcomes:
                    self._record(old, OutcomeState.CANCELLED, "superseded by newer source request")
            envelope = HostEnvelope(self._session_id, self._session_generation,
                                    source_id, self._source_generations.get(source_id, 0),
                                    request_id, now_ms, now_ms + ttl_ms,
                                    self._capabilities.digest)
            self._requests[request_id] = envelope
            return envelope

    def expire(self, *, now_ms: int) -> tuple[Outcome, ...]:
        with self._lock:
            self._clock(now_ms)
            changed = []
            for request_id, envelope in self._requests.items():
                outcome = self._outcomes.get(request_id)
                deadline = outcome.lease_expires_at_ms if outcome else envelope.expires_at_ms
                if (outcome is None or outcome.state == OutcomeState.SHADOW_ACCEPTED) and now_ms >= deadline:
                    changed.append(self._record(envelope, OutcomeState.EXPIRED,
                                               "shadow lease expired; no physical outcome observed",
                                               outcome.intent if outcome else None))
            return tuple(changed)

    def _cancel(self, *, source_id: str | None, reason: str) -> None:
        for request_id, envelope in self._requests.items():
            outcome = self._outcomes.get(request_id)
            if ((source_id is None or envelope.source_id == source_id)
                    and (outcome is None or outcome.state == OutcomeState.SHADOW_ACCEPTED)):
                self._record(envelope, OutcomeState.CANCELLED, reason,
                             outcome.intent if outcome else None)

    def cancel_source(self, source_id: str, *, now_ms: int) -> None:
        with self._lock:
            self._clock(now_ms)
            text_field(source_id, "source_id")
            self._source_generations[source_id] = self._source_generations.get(source_id, 0) + 1
            self._cancel(source_id=source_id, reason="source cancelled")

    def request_stop(self, *, now_ms: int) -> Outcome:
        """Trusted stop-button path: no LLM, network, parsing or physical claim."""
        with self._lock:
            self._clock(now_ms)
            self._session_generation += 1
            self._stop_latched = True
            self._cancel(source_id=None, reason="deterministic stop request")
            return Outcome(OutcomeState.STOP_REQUESTED,
                           "pending/shadow-active proposals cancelled; physical stop is unconfirmed")

    def replace_capabilities(self, capabilities: CapabilitySnapshot, *, now_ms: int) -> None:
        with self._lock:
            self._clock(now_ms)
            if type(capabilities) is not CapabilitySnapshot:
                raise ContractError("capabilities must be a CapabilitySnapshot")
            if capabilities.digest != self._capabilities.digest:
                self._session_generation += 1
                self._cancel(source_id=None, reason="active capability snapshot changed")
                self._capabilities = capabilities

    def outcome(self, request_id: str, *, now_ms: int) -> Outcome | None:
        with self._lock:
            self.expire(now_ms=now_ms)
            return self._outcomes.get(request_id)

    def submit(self, envelope: HostEnvelope, raw: str, *, now_ms: int,
               authorization: ShadowAuthorization | None = None) -> Outcome:
        with self._lock:
            self.expire(now_ms=now_ms)
            if type(envelope) is not HostEnvelope or self._requests.get(envelope.request_id) != envelope:
                return Outcome(OutcomeState.REJECTED, "unknown or modified host envelope")
            prior = self._outcomes.get(envelope.request_id)
            if prior is not None:
                # Do not overwrite the original result or renew a lease on replay.
                return Outcome(OutcomeState.REJECTED, f"request already resolved: {prior.state.value}", envelope)
            if (self._stop_latched or envelope.session_id != self._session_id
                    or envelope.session_generation != self._session_generation
                    or envelope.source_generation != self._source_generations.get(envelope.source_id, 0)):
                return self._record(envelope, OutcomeState.REJECTED, "stale session/source generation")
            if envelope.capability_digest != self._capabilities.digest:
                return self._record(envelope, OutcomeState.REJECTED, "capability identity changed")
            try:
                intent = parse_intent(raw)
            except ContractError as exc:
                return self._record(envelope, OutcomeState.REJECTED, f"contract: {exc}")
            if isinstance(intent, StopRequest):
                self.request_stop(now_ms=now_ms)
                return self._record(envelope, OutcomeState.STOP_REQUESTED,
                                    "model requested stop; physical stop is unconfirmed", intent)
            if isinstance(intent, Clarify):
                return self._record(envelope, OutcomeState.CLARIFY, intent.question, intent)
            if isinstance(intent, Unsupported):
                return self._record(envelope, OutcomeState.UNSUPPORTED, intent.reason, intent)
            if (type(authorization) is not ShadowAuthorization
                    or authorization.session_id != self._session_id
                    or authorization.source_id != envelope.source_id
                    or authorization.capability_digest != self._capabilities.digest
                    or now_ms >= authorization.expires_at_ms):
                return self._record(envelope, OutcomeState.REJECTED, "missing/current-session shadow authorization required", intent)
            if self._capabilities.requires_unqualified_test_opt_in and not authorization.allow_unqualified_metadata:
                return self._record(envelope, OutcomeState.REJECTED, "unqualified metadata requires explicit shadow-test opt-in", intent)
            for axis in _AXES:
                low, high = getattr(self._capabilities, axis)
                if not low <= getattr(intent, axis) <= high:
                    return self._record(envelope, OutcomeState.REJECTED, f"outside active {axis} bounds; not clamped", intent)
            if intent.duration_ms > self._capabilities.max_duration_ms:
                return self._record(envelope, OutcomeState.REJECTED, "duration exceeds active shadow capability", intent)
            lease = now_ms + intent.duration_ms
            if lease > min(envelope.expires_at_ms, authorization.expires_at_ms):
                return self._record(envelope, OutcomeState.REJECTED, "duration outlives original request/authorization deadline", intent)
            if any(outcome.state == OutcomeState.SHADOW_ACCEPTED for outcome in self._outcomes.values()):
                return self._record(envelope, OutcomeState.REJECTED, "another source/request owns the shadow lease", intent)
            return self._record(envelope, OutcomeState.SHADOW_ACCEPTED,
                                "offline proposal only; no actuation or physical success claimed", intent, lease)
