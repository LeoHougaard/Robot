"""One-use, host-clock-fenced commands for COMPUTE-ONLY runtime experiments.

This is not an actuator adapter. A frame ticket grants only a diagnostic
calculation. It must never be used as robot authorization or a heartbeat.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time
from typing import Any, Callable

from .contract import VelocityRequest, integer
from .session import ShadowSession
from .supervisor import CapabilitySnapshot, OutcomeState


class BridgeError(ValueError):
    """Sanitized, fixed error codes for the compute-only boundary."""


@dataclass(frozen=True)
class FrameTicket:
    session_id: str
    request_id: str
    capability_digest: str
    generation: int
    sample_sequence: int
    observed_at_ms: int
    expires_at_ms: int
    command: tuple[float, float, float]


class ShadowCommandBridge:
    """Wrap the real session and consume its authoritative supervisor outcomes.

    Slow actor work happens between begin_frame and finish_frame, outside this
    lock. Cancellation can therefore fence a result without waiting for actor
    computation. Tickets are one-use object identities, not serialized grants.
    No transport, arming method, background heartbeat or physical mode exists.
    """
    def __init__(self, capabilities: CapabilitySnapshot, *, allow_shadow: bool = False,
                 allow_unqualified_metadata: bool = False,
                 clock_ms: Callable[[], int] = lambda: time.monotonic_ns() // 1_000_000,
                 max_sample_age_ms: int = 40, request_ttl_ms: int = 10_000,
                 mode: str = "manual", worker_factory: Any = None):
        integer(max_sample_age_ms, "max_sample_age_ms", minimum=1, maximum=1_000)
        self._lock = threading.RLock()
        self._clock = clock_ms
        self._max_age = max_sample_age_ms
        self._generation = 0
        self._request_id: str | None = None
        self._ticket: FrameTicket | None = None
        self._last_sequence = -1
        self._last_observed_at = -1
        self._last_now = 0
        self._events: deque[dict[str, Any]] = deque(maxlen=512)
        self._session = ShadowSession(
            capabilities, allow_shadow=allow_shadow,
            allow_unqualified_metadata=allow_unqualified_metadata,
            clock_ms=clock_ms, emit=self._capture, request_ttl_ms=request_ttl_ms,
            mode=mode, worker_factory=worker_factory,
        )

    @property
    def capabilities(self) -> CapabilitySnapshot:
        return self._session.supervisor.capabilities

    def _invalidate(self) -> None:
        self._generation += 1
        self._ticket = None
        self._request_id = None

    def _capture(self, event: dict[str, Any]) -> None:
        # Only our owned ShadowSession invokes this; arbitrary serialized events
        # are never accepted as admission evidence.
        if event["event"] == "request_started":
            self._invalidate()
        elif event["event"] == "outcome":
            if event["state"] == OutcomeState.SHADOW_ACCEPTED.value:
                self._invalidate()
                self._request_id = event["request_id"]
            elif event["request_id"] == self._request_id or event["state"] == "stop_requested":
                self._invalidate()
        elif event["event"] in ("cancel_requested", "session_closed", "capabilities_selected"):
            self._invalidate()
        self._events.append(event)

    def take_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._events)
            self._events.clear()
            return events

    def _now(self) -> int:
        now = self._clock()
        if type(now) is not int or now < self._last_now:
            self._invalidate()
            self._session.close("bridge_clock_failed")
            raise BridgeError("bridge_clock_failed")
        self._last_now = now
        return now

    def handle_line(self, line: str) -> bool:
        """The same trusted-operator protocol as ShadowSession, never model text."""
        with self._lock:
            return self._session.handle_line(line)

    def propose(self, raw: str) -> str:
        with self._lock:
            return self._session.propose(raw)

    def request(self, text: str) -> str:
        with self._lock:
            return self._session.request(text)

    def poll(self) -> None:
        with self._lock:
            self._now()
            self._session.poll()

    def cancel(self) -> None:
        with self._lock:
            self._invalidate()
            self._session.cancel()

    def stop(self) -> None:
        with self._lock:
            self._invalidate()
            self._session.stop()

    def close(self) -> None:
        with self._lock:
            self._invalidate()
            self._session.close()

    def replace_capabilities(self, capabilities: CapabilitySnapshot) -> None:
        with self._lock:
            self._session.replace_capabilities(capabilities)

    def _active(self, now: int):
        if self._request_id is None:
            raise BridgeError("no_current_shadow_lease")
        outcome = self._session.supervisor.outcome(self._request_id, now_ms=now)
        if (outcome is None or outcome.state != OutcomeState.SHADOW_ACCEPTED
                or type(outcome.intent) is not VelocityRequest
                or outcome.envelope.capability_digest != self.capabilities.digest
                or now >= outcome.lease_expires_at_ms):
            self._invalidate()
            raise BridgeError("no_current_shadow_lease")
        return outcome

    def begin_frame(self, *, sample_sequence: int, observed_at_ms: int) -> FrameTicket:
        with self._lock:
            self.poll()
            now = self._now()
            outcome = self._active(now)
            if self._ticket is not None:
                raise BridgeError("frame_already_in_flight")
            try:
                integer(sample_sequence, "sample_sequence")
                integer(observed_at_ms, "observed_at_ms")
            except ValueError:
                self.cancel()
                raise BridgeError("invalid_sample_identity") from None
            if (sample_sequence <= self._last_sequence
                    or observed_at_ms <= self._last_observed_at
                    or observed_at_ms > now or now - observed_at_ms > self._max_age):
                self.cancel()
                raise BridgeError("stale_duplicate_or_future_sample")
            intent = outcome.intent
            ticket = FrameTicket(
                outcome.envelope.session_id, outcome.envelope.request_id,
                outcome.envelope.capability_digest, self._generation,
                sample_sequence, observed_at_ms, outcome.lease_expires_at_ms,
                (intent.forward_m_s, intent.lateral_m_s, intent.yaw_rate_rad_s),
            )
            self._last_sequence = sample_sequence
            self._last_observed_at = observed_at_ms
            self._ticket = ticket
            return ticket

    def finish_frame(self, ticket: FrameTicket) -> None:
        """Fence the result immediately before returning a diagnostic to a caller."""
        with self._lock:
            self.poll()
            now = self._now()
            # A late result/error cannot cancel or consume a newer frame.
            if ticket is not self._ticket:
                raise BridgeError("frame_cancelled_or_stale")
            self._ticket = None
            outcome = self._active(now)
            if (ticket.generation != self._generation
                    or ticket.request_id != outcome.envelope.request_id
                    or ticket.capability_digest != self.capabilities.digest
                    or now >= ticket.expires_at_ms
                    or now - ticket.observed_at_ms > self._max_age):
                self.cancel()
                raise BridgeError("frame_cancelled_stale_or_expired")

    def fail_frame(self, ticket: FrameTicket) -> None:
        with self._lock:
            if ticket is self._ticket:
                self.cancel()
