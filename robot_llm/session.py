"""Interactive, strictly non-actuating shadow sessions (JSONL on stdin/stdout).

The trusted host owns time, envelopes, grants and cancellation. A model produces
one completed intent, never an operation on this session. No robot integration.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, dataclass
import json
import multiprocessing
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Callable, Protocol
import uuid

from .contract import (ContractError, MAX_RESPONSE_CHARS, integer, intent_to_dict,
                       parse_intent, strict_json_object, text_field)
from .providers import EndpointConfig, InferenceProvider, MODES, ProviderResult
from .supervisor import (CapabilitySnapshot, HostEnvelope, Outcome, OutcomeState,
                         ShadowAuthorization, ShadowSupervisor)


class SessionError(ValueError):
    """Fixed diagnostic codes only; never provider diagnostics or credentials."""


class PendingCall(Protocol):
    def poll(self) -> ProviderResult | None: ...
    def close(self) -> None: ...


def capability_context(caps: CapabilitySnapshot) -> dict[str, Any]:
    return {"frame": "body", "forward_m_s": list(caps.forward_m_s),
            "lateral_m_s": list(caps.lateral_m_s),
            "yaw_rate_rad_s": list(caps.yaw_rate_rad_s),
            "max_duration_ms": caps.max_duration_ms, "shadow_only": True,
            "capability_digest": caps.digest}


def _generate(mode: str, config: EndpointConfig, text: str,
              caps: dict[str, Any]) -> ProviderResult:
    return InferenceProvider(mode, config).generate(text, caps)


def _child_generate(sender: Any, target: Callable[..., ProviderResult], args: tuple) -> None:
    # Third-party parser/transport diagnostics must not enter JSONL or expose keys.
    with open(os.devnull, "w", encoding="utf-8") as sink, redirect_stdout(sink), redirect_stderr(sink):
        # BAML includes native code; replacing sys.stdout alone misses os.write,
        # C/Rust diagnostics and inherited file descriptors.
        os.dup2(sink.fileno(), 1)
        os.dup2(sink.fileno(), 2)
        try:
            result = target(*args)
            if type(result) is not ProviderResult:
                raise TypeError()
            sender.send(result)
        except BaseException:
            try:
                sender.send(ProviderResult(None, 0, error="provider_failed"))
            except (BrokenPipeError, EOFError, OSError):
                pass
        finally:
            sender.close()


class ProcessCall:
    """One disposable provider process; cancellation terminates and reaps it.

    Unlike a thread around a synchronous HTTP library, this leaves no in-flight
    local worker after close, even when DNS, HTTP headers or a parser blocks.
    Killing the client cannot retract a request already received by a server.
    """
    def __init__(self, target: Callable[..., ProviderResult], args: tuple):
        context = multiprocessing.get_context("spawn")
        self._receiver, sender = context.Pipe(duplex=False)
        self._process = context.Process(target=_child_generate, args=(sender, target, args),
                                        name="robot-llm-inference", daemon=True)
        self._closed = False
        try:
            self._process.start()
        except BaseException:
            # start() may fail or be interrupted after creating the OS process.
            self.close()
            raise
        finally:
            sender.close()

    def poll(self) -> ProviderResult | None:
        if self._closed:
            return None
        try:
            if self._receiver.poll():
                result = self._receiver.recv()
                return result if type(result) is ProviderResult else ProviderResult(None, 0, error="provider_failed")
            if not self._process.is_alive():
                return ProviderResult(None, 0, error="provider_exited_without_result")
        except (EOFError, OSError):
            return ProviderResult(None, 0, error="provider_failed")
        return None

    def close(self) -> None:
        if self._closed:
            return
        if self._process.pid is not None:
            if self._process.is_alive():
                self._process.terminate()
            self._process.join(timeout=1)
            if self._process.is_alive():
                self._process.kill()
                self._process.join()  # Do not claim closure while a local worker remains.
        self._receiver.close()
        self._process.close()
        self._closed = True


class LiveInference:
    def __init__(self, mode: str, config: EndpointConfig):
        if mode not in MODES or config.allow_network is not True:
            raise SessionError("live_inference_requires_explicit_network_opt_in")
        self.mode, self.config = mode, config

    def __call__(self, text: str, caps: dict[str, Any]) -> ProcessCall:
        return ProcessCall(_generate, (self.mode, self.config, text, caps))


@dataclass
class _Pending:
    envelope: HostEnvelope
    authorization: ShadowAuthorization
    call: PendingCall


class ShadowSession:
    """Single trusted operator, latest-request-wins, bounded session lifetime.

    Methods and provider completion are serialized. No callback may reenter the
    session. The event callback must be fast and nonblocking. Call poll regularly
    (the CLI does), or use the injected clock/pending-call seams in offline tests.
    This class itself creates no timer thread and has no physical transport.
    """
    def __init__(self, capabilities: CapabilitySnapshot, *, allow_shadow: bool = False,
                 allow_unqualified_metadata: bool = False, mode: str = "manual",
                 worker_factory: Callable[[str, dict[str, Any]], PendingCall] | None = None,
                 clock_ms: Callable[[], int] = lambda: time.monotonic_ns() // 1_000_000,
                 emit: Callable[[dict[str, Any]], None] = lambda event: None,
                 request_ttl_ms: int = 10_000, max_requests: int = 1000):
        if allow_shadow is not True:
            raise SessionError("explicit_shadow_opt_in_required")
        if type(allow_unqualified_metadata) is not bool:
            raise SessionError("invalid_shadow_metadata_opt_in")
        if mode not in ("manual", *MODES) or ((mode == "manual") != (worker_factory is None)):
            raise SessionError("invalid_mode_or_worker_configuration")
        integer(request_ttl_ms, "request_ttl_ms", minimum=1, maximum=60_000)
        integer(max_requests, "max_requests", minimum=1, maximum=10_000)
        self.session_id = str(uuid.uuid4())
        self.supervisor = ShadowSupervisor(session_id=self.session_id, capabilities=capabilities)
        self.mode, self._worker_factory = mode, worker_factory
        self._clock_ms, self._emit = clock_ms, emit
        self._allow_unqualified = allow_unqualified_metadata
        self._ttl_ms, self._max_requests = request_ttl_ms, max_requests
        self._lock = threading.RLock()
        self._pending: _Pending | None = None
        self._open: dict[str, OutcomeState | None] = {}
        self._counter = self._sequence = self._last_now = 0
        self._closed = self._stopped = False
        self._event("session_opened", self._now(), capability_digest=capabilities.digest,
                    evidence_kind=("manual_proposal_not_model_inference" if mode == "manual"
                                   else "opt_in_model_inference"),
                    unqualified_metadata_opt_in=allow_unqualified_metadata)

    def _now(self) -> int:
        now = self._clock_ms()
        if type(now) is not int or now < self._last_now:
            raise SessionError("monotonic_clock_failed")
        self._last_now = now
        return now

    def _event(self, event: str, now: int, **fields: Any) -> None:
        self._sequence += 1
        self._emit({"event_version": 1, "event": event, "sequence": self._sequence,
                    "session_id": self.session_id, "monotonic_ms": now,
                    "mode": "SHADOW", "provider_mode": self.mode,
                    "physical_execution": False, "physical_stop_confirmed": False, **fields})

    def _report(self, outcome: Outcome, now: int) -> None:
        request_id = outcome.envelope.request_id if outcome.envelope else None
        self._event("outcome", now, request_id=request_id, state=outcome.state.value,
                    # Contract errors can quote arbitrary invalid object keys.
                    reason=("response_failed_strict_contract" if outcome.reason.startswith("contract:")
                            else outcome.reason),
                    intent=intent_to_dict(outcome.intent) if outcome.intent else None,
                    lease_expires_at_ms=outcome.lease_expires_at_ms)
        if request_id:
            if outcome.state == OutcomeState.SHADOW_ACCEPTED:
                self._open[request_id] = outcome.state
            else:
                self._open.pop(request_id, None)

    def _changes(self, now: int) -> None:
        self.supervisor.expire(now_ms=now)
        for request_id, prior in list(self._open.items()):
            outcome = self.supervisor.outcome(request_id, now_ms=now)
            if outcome and outcome.state != prior:
                self._report(outcome, now)

    def _discard_worker(self) -> None:
        if self._pending:
            self._pending.call.close()
            self._pending = None

    def _ready(self) -> None:
        if self._closed or self._stopped:
            raise SessionError("session_closed" if self._closed else "stop_latched_create_new_session")
        if self._counter >= self._max_requests:
            raise SessionError("session_request_limit_reached_create_new_session")

    def _begin(self, now: int) -> tuple[HostEnvelope, ShadowAuthorization]:
        self._ready()
        # Explicitly cancel even an accepted shadow lease before replacement.
        self.supervisor.cancel_source("operator", now_ms=now)
        try:
            self._changes(now)
        finally:
            self._discard_worker()
        # Process cleanup time is part of this new request only after issuance.
        now = self._now()
        self._counter += 1
        envelope = self.supervisor.begin_request(source_id="operator", request_id=f"r{self._counter}",
                                                 now_ms=now, ttl_ms=self._ttl_ms)
        grant = ShadowAuthorization(self.session_id, "operator", self.supervisor.capabilities.digest,
                                    envelope.expires_at_ms, self._allow_unqualified)
        self._open[envelope.request_id] = None
        self._event("request_started", now, request_id=envelope.request_id, envelope=asdict(envelope))
        return envelope, grant

    def request(self, text: str) -> str:
        with self._lock:
            if self.mode == "manual":
                raise SessionError("manual_mode_requires_authored_proposal_not_natural_language")
            text_field(text, "text", maximum=8000)
            envelope, grant = self._begin(self._now())
            try:
                call = self._worker_factory(text, capability_context(self.supervisor.capabilities))
                self._pending = _Pending(envelope, grant, call)
            except Exception:
                self._provider_failed("provider_start_failed", self._now())
            return envelope.request_id

    def propose(self, raw: str) -> str:
        with self._lock:
            if self.mode != "manual":
                raise SessionError("manual_proposals_require_manual_mode")
            if type(raw) is not str or len(raw) > MAX_RESPONSE_CHARS:
                raise SessionError("invalid_manual_proposal_size_or_type")
            envelope, grant = self._begin(self._now())
            self._submit(envelope, grant, ProviderResult(raw, 0, synthetic=True), self._now())
            return envelope.request_id

    def _provider_failed(self, code: str, now: int) -> None:
        self.supervisor.cancel_source("operator", now_ms=now)
        try:
            self._changes(now)
            self._event("provider_error", now, code=code)
        finally:
            self._discard_worker()

    def _submit(self, envelope: HostEnvelope, grant: ShadowAuthorization,
                result: ProviderResult, now: int) -> None:
        # Provider latency, proposed timestamps and parser repair never set time.
        self._changes(now)
        if self.supervisor.outcome(envelope.request_id, now_ms=now) is not None:
            return
        if type(result) is not ProviderResult:
            self._provider_failed("invalid_provider_result", now)
            return
        if result.error or result.timed_out or type(result.raw_content) is not str:
            self._provider_failed("provider_timeout" if result.timed_out else "provider_failed", now)
            return
        if result.repair_status == "parse_error":
            self._provider_failed("parser_failed", now)
            return
        if result.candidate_content is not None:
            try:
                original = parse_intent(result.raw_content)
            except ContractError:
                pass  # Let the independent supervisor reject the original unchanged.
            else:
                try:
                    equal = intent_to_dict(original) == intent_to_dict(parse_intent(result.candidate_content))
                except ContractError:
                    equal = False
                if not equal:
                    self._provider_failed("parser_changed_valid_original", now)
                    return
        outcome = self.supervisor.submit(envelope, result.raw_content, now_ms=now, authorization=grant)
        outcome = self.supervisor.outcome(envelope.request_id, now_ms=now) or outcome
        self._report(outcome, now)
        if outcome.state == OutcomeState.STOP_REQUESTED:
            self._stopped = True

    def poll(self) -> None:
        with self._lock:
            if self._closed:
                return
            now = self._now()
            self._changes(now)
            if not self._pending:
                return
            pending = self._pending
            if self.supervisor.outcome(pending.envelope.request_id, now_ms=now) is not None:
                self._discard_worker()
                return
            try:
                result = pending.call.poll()
            except Exception:
                self._provider_failed("provider_poll_failed", self._now())
                return
            if result is not None:
                self._discard_worker()
                # Include polling, IPC and cleanup time before admission.
                now = self._now()
                self._submit(pending.envelope, pending.authorization, result, now)

    def cancel(self) -> None:
        with self._lock:
            if self._closed:
                raise SessionError("session_closed")
            now = self._now()
            self.supervisor.cancel_source("operator", now_ms=now)
            try:
                self._changes(now)
                self._event("cancel_requested", now)
            finally:
                self._discard_worker()

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                raise SessionError("session_closed")
            now = self._now()
            # Latch and report before waiting for any provider process cleanup.
            outcome = self.supervisor.request_stop(now_ms=now)
            self._stopped = True
            try:
                self._changes(now)
                self._report(outcome, now)
            finally:
                self._discard_worker()

    def replace_capabilities(self, capabilities: CapabilitySnapshot) -> None:
        with self._lock:
            self._ready()
            now = self._now()
            changed = capabilities.digest != self.supervisor.capabilities.digest
            self.supervisor.replace_capabilities(capabilities, now_ms=now)
            try:
                self._changes(now)
            finally:
                if changed:
                    self._discard_worker()
            self._event("capabilities_selected", now, capability_digest=capabilities.digest)

    def status(self) -> None:
        with self._lock:
            self.poll()
            self._event("status", self._now(), stopped=self._stopped, closed=self._closed,
                        pending_request_id=self._pending.envelope.request_id if self._pending else None,
                        capability_digest=self.supervisor.capabilities.digest,
                        request_count=self._counter)

    def close(self, reason: str = "operator_close") -> None:
        with self._lock:
            if self._closed:
                return
            # Still fail closed if an injected/broken clock can no longer advance.
            try:
                now = self._now()
            except Exception:
                now = self._last_now
            self.supervisor.request_stop(now_ms=now)
            self._stopped = True
            try:
                self._changes(now)
            finally:
                self._discard_worker()
                self._closed = True
            self._event("session_closed", now, reason=reason)

    def handle_line(self, line: str) -> bool:
        """Return False only for close. Invalid commands cancel pending/active work.

        Protocol input is a trusted operator channel, never model/vision output.
        Unknown fields, duplicate keys and invalid JSON are rejected as a whole.
        """
        try:
            command = strict_json_object(line)
            op = command.get("op")
            if type(op) is not str:
                raise SessionError("invalid_command")
            required = {"request": {"op", "text"}, "propose": {"op", "raw"},
                        "cancel": {"op"}, "stop": {"op"}, "status": {"op"}, "close": {"op"}}
            if op not in required or set(command) != required[op]:
                raise SessionError("invalid_command")
            if op == "request":
                self.request(command["text"])
            elif op == "propose":
                self.propose(command["raw"])
            elif op == "close":
                self.close()
                return False
            else:
                getattr(self, op)()
        except (ContractError, SessionError):
            with self._lock:
                if not self._closed:
                    self.cancel()
                self._event("command_error", self._now(), code="invalid_command_or_session_state")
        return True


def run_jsonl(session: ShadowSession, input_stream: Any, *, poll_interval_s: float = .02) -> int:
    """Main thread reads input; a stoppable monitor handles results and expiry.

    Only the main thread blocks on stdin. EOF and Ctrl-C cancel work immediately;
    a pipe must remain open to receive inference rather than cancelling on EOF.
    """
    done = threading.Event()
    failed = threading.Event()

    def monitor() -> None:
        try:
            while not done.wait(poll_interval_s):
                session.poll()
        except Exception:
            failed.set()
            try:
                session.close("session_monitor_failed")
            except Exception:
                pass  # Broken output must not prevent worker cleanup or print diagnostics.
            finally:
                cancel_read = getattr(input_stream, "cancel_read", None)
                if cancel_read is not None:
                    cancel_read()

    thread = threading.Thread(target=monitor, name="robot-llm-shadow-monitor")
    reason, code = "eof", 0
    thread.start()
    try:
        while not failed.is_set():
            line = input_stream.readline(MAX_RESPONSE_CHARS + 2)
            if not line:
                break
            if len(line) > MAX_RESPONSE_CHARS:
                reason, code = "input_size_limit", 2
                break
            if not session.handle_line(line):
                reason = "operator_close"
                break
    except KeyboardInterrupt:
        reason, code = "keyboard_interrupt", 130
    except Exception:
        reason, code = "session_io_failed", 2
    finally:
        done.set()
        try:
            session.close(reason)
        except Exception:
            code = 2
        finally:
            thread.join()
    return 2 if failed.is_set() else code


class NonblockingJsonlIO:
    """Bounded UTF-8 descriptor I/O; no blocked reader/writer helper threads.

    The CLI requires OS support for nonblocking stdin/stdout. Consumers must
    keep draining output. Backpressure fails closed instead of delaying stop.
    A truncated final line after partial write must be discarded by consumers.
    """
    def __init__(self, input_fd: int, output_fd: int):
        self._input_fd, self._output_fd = input_fd, output_fd
        self._buffer = bytearray()
        self._cancel = threading.Event()
        self._previous: dict[int, bool] = {}
        try:
            # Descriptor aliases can share an open-file description (for example
            # terminal stdin/stdout). Snapshot all flags before mutating any.
            for fd in (input_fd, output_fd):
                self._previous[fd] = os.get_blocking(fd)
            for fd in self._previous:
                os.set_blocking(fd, False)
        except (OSError, AttributeError):
            self.close()
            raise SessionError("nonblocking_stdio_unavailable") from None

    def emit(self, event: dict[str, Any]) -> None:
        data = (json.dumps(event, ensure_ascii=True, allow_nan=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            if os.write(self._output_fd, data) != len(data):
                raise SessionError("output_backpressure")
        except (BlockingIOError, BrokenPipeError, OSError):
            raise SessionError("output_unavailable_or_backpressure") from None

    def readline(self, limit: int) -> str:
        while not self._cancel.is_set():
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                line = bytes(self._buffer[:newline + 1])
                del self._buffer[:newline + 1]
                return line.decode("utf-8")
            if len(self._buffer) > limit * 4:
                return " " * limit  # Oversized input closes the session, no unbounded drain.
            try:
                chunk = os.read(self._input_fd, 4096)
            except BlockingIOError:
                self._cancel.wait(.02)
                continue
            if not chunk:
                line = bytes(self._buffer)
                self._buffer.clear()
                return line.decode("utf-8")
            self._buffer.extend(chunk)
        return ""

    def cancel_read(self) -> None:
        self._cancel.set()

    def close(self) -> None:
        self.cancel_read()
        for fd, blocking in self._previous.items():
            try:
                os.set_blocking(fd, blocking)
            except OSError:
                pass
        self._previous.clear()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("manual", *MODES), default="manual")
    parser.add_argument("--capabilities", type=Path, required=True, help="explicit host-selected bundle metadata JSON")
    parser.add_argument("--allow-shadow", action="store_true", help="grant this session shadow-only proposal admission")
    parser.add_argument("--allow-unqualified-metadata", action="store_true", help="explicit offline/shadow test of unpromoted or unverified metadata")
    parser.add_argument("--max-duration-ms", type=int, default=1000)
    parser.add_argument("--request-ttl-ms", type=int, default=10_000)
    parser.add_argument("--max-requests", type=int, default=1000)
    parser.add_argument("--allow-network", action="store_true", help="allow inference at the exact configured endpoint")
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env", help="read only this named existing environment variable; never save a key")
    parser.add_argument("--schema-transport", choices=("response-format", "structured-outputs"), default="response-format")
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument("--max-tokens", type=int, default=512)
    args = parser.parse_args(argv)
    stream = None
    try:
        factory = None
        if args.mode == "manual":
            if args.allow_network or args.base_url or args.model or args.api_key_env:
                raise SessionError("manual_mode_rejects_network_options")
        else:
            if not (args.allow_network and args.base_url and args.model):
                raise SessionError("live_mode_requires_network_opt_in_endpoint_and_model")
            key = os.environ.get(args.api_key_env) if args.api_key_env else None
            if args.api_key_env and not key:
                raise SessionError("specified_key_environment_variable_is_empty")
            config = EndpointConfig(args.base_url, args.model, True, args.timeout_seconds,
                                    args.max_tokens, key, args.schema_transport)
            factory = LiveInference(args.mode, config)
        caps = CapabilitySnapshot.from_metadata_json(args.capabilities.read_text(encoding="utf-8"),
                                                    max_duration_ms=args.max_duration_ms)
        stream = NonblockingJsonlIO(sys.stdin.fileno(), sys.stdout.fileno())
        session = ShadowSession(caps, allow_shadow=args.allow_shadow,
                                allow_unqualified_metadata=args.allow_unqualified_metadata,
                                mode=args.mode, worker_factory=factory, emit=stream.emit,
                                request_ttl_ms=args.request_ttl_ms, max_requests=args.max_requests)
    except Exception as exc:
        if stream is not None:
            stream.close()
        # Only fixed session codes; no paths, endpoints, API-key names or diagnostics.
        code = str(exc) if isinstance(exc, SessionError) else "invalid_configuration"
        print(json.dumps({"event": "configuration_error", "code": code,
                          "mode": "SHADOW", "physical_execution": False}), file=sys.stderr)
        return 2
    try:
        return run_jsonl(session, stream)
    finally:
        stream.close()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
