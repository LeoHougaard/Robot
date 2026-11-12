#!/usr/bin/env python3
"""Offline, dependency-free torque-off dog12 capture acceptance. Never opens USB.

See HARDWARE-ACCEPTANCE.md for the capture and report contracts. PASS describes
supplied evidence only, never deployment approval or independently proven USB
health. Parsed Pixel records cannot establish byte-framing or callback age.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any

SCHEMA_VERSION = 1
EXPECTED_FIRMWARE = "0.1.18-transportguard"
IDS = set(range(1, 13))
MAX_FILE_BYTES = 128 * 1024 * 1024
MAX_LINE_BYTES = 1024 * 1024
MAX_WIRE_LINE = 16_384
WARMUP = 50
MIN_FRAMES = 1250
UINT32 = 0xFFFF_FFFF
SAFE_CAPTURE_COMMANDS = {"hello", "policy_monitor", "policy_monitor_frame", "policy_disarm", "stop"}
EXIT = {"PASS": 0, "FAIL": 1, "BLOCKED": 2}


def uint(value: Any, maximum: int = UINT32) -> bool:
    return type(value) is int and 0 <= value <= maximum


def number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def stats(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    def percentile(fraction: float):
        return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None
    return {"count": len(ordered), "min": min(ordered, default=None),
            "median": percentile(.5), "p99": percentile(.99),
            "max": max(ordered, default=None)}


def strict_json(value: str | bytes) -> Any:
    def pairs(items):
        result = {}
        for key, item in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = item
        return result
    def bad_constant(item):
        raise ValueError("nonfinite JSON number")
    result = json.loads(value, object_pairs_hook=pairs, parse_constant=bad_constant)
    def finite(item):
        if isinstance(item, float) and not math.isfinite(item):
            raise ValueError("nonfinite JSON number")
        if isinstance(item, dict):
            for child in item.values(): finite(child)
        if isinstance(item, list):
            for child in item: finite(child)
    finite(result)
    return result


class WireDecoder:
    """Bounded strict JSON-line decoder, independent of read chunk boundaries.

    Mirrors newline/padding semantics, but reports malformed/oversized/dangling
    lines instead of silently dropping them. Timestamp belongs to final byte.
    """
    def __init__(self, *, allow_initial_abort=False):
        self.allow_initial_abort = allow_initial_abort
        self.abort_pending = False
        self.abort_nonce = None
        self.pending = bytearray()
        self.discarding = False
        self.counts = Counter()
        self.events = []

    def feed(self, chunk: bytes) -> list[dict]:
        result = []
        self.events = []
        self.counts["bytes"] += len(chunk)
        self.counts["chunks"] += 1
        for position, byte in enumerate(chunk):
            if (byte == 0x18 and self.allow_initial_abort and not self.pending
                    and not self.counts["nonempty_lines"] and not self.counts["input_abort_preambles"]):
                self.counts["input_abort_preambles"] += 1
                self.abort_pending = True
                continue
            if byte == 10:
                if self.abort_pending:
                    nonce = bytes(self.pending)
                    if len(nonce) == 32 and all(c in b"0123456789abcdef" for c in nonce):
                        self.abort_nonce = nonce.decode("ascii")
                    else:
                        self.counts["invalid_lines"] += 1
                    self.abort_pending = False
                    self.pending.clear()
                    continue
                if self.discarding:
                    self.discarding = False
                else:
                    raw = bytes(self.pending).strip()
                    if raw:
                        self.counts["nonempty_lines"] += 1
                        try:
                            value = strict_json(raw.decode("utf-8", errors="strict"))
                            if not isinstance(value, dict): raise ValueError("not an object")
                            result.append(value)
                            self.events.append(("message", value, position + 1))
                            self.counts["valid_lines"] += 1
                        except (ValueError, UnicodeError, RecursionError):
                            self.counts["invalid_lines"] += 1
                            self.events.append(("error", "invalid_lines", position + 1))
                    else:
                        self.counts["padding_lines"] += 1
                self.pending.clear()
            elif not self.discarding:
                self.pending.append(byte)
                if len(self.pending) > MAX_WIRE_LINE:
                    self.counts["oversized_lines"] += 1
                    self.pending.clear()
                    self.discarding = True
        return result

    def finish(self):
        # Whitespace padding after the final LF is harmless; a nonempty JSON
        # object without its LF is still an incomplete wire frame.
        if self.discarding or bytes(self.pending).strip():
            self.counts["truncated_lines"] += 1
        self.pending.clear()
        self.discarding = False


def read_capture(path: Path) -> tuple[list[dict], dict]:
    """Only explicit regular local files; refuse devices/FIFOs before opening."""
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("capture must be a regular file, not a symlink/device/FIFO")
    if before.st_size > MAX_FILE_BYTES:
        raise ValueError("capture exceeds the 128 MiB limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    records, digest, total = [], hashlib.sha256(), 0
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode) or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise ValueError("capture changed while opening")
        line_number = 0
        while line := stream.readline(MAX_LINE_BYTES + 1):
            line_number += 1
            total += len(line)
            if len(line) > MAX_LINE_BYTES or total > MAX_FILE_BYTES:
                raise ValueError("capture exceeds a line or total size limit")
            digest.update(line)
            if not line.endswith(b"\n"):
                raise ValueError(f"line {line_number}: truncated capture record (missing newline)")
            if not line.strip(): continue
            try:
                record = strict_json(line.decode("utf-8"))
            except (ValueError, UnicodeError, RecursionError) as error:
                raise ValueError(f"line {line_number}: invalid capture JSON: {error}") from error
            if not isinstance(record, dict):
                raise ValueError(f"line {line_number}: capture record must be an object")
            records.append(record)
        after = os.fstat(stream.fileno())
        if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("capture changed during analysis")
    return records, {"path": str(path), "sha256": digest.hexdigest(), "bytes": total}


def status_of(checks: list[dict]) -> str:
    return "FAIL" if any(c["status"] == "FAIL" for c in checks) else (
        "BLOCKED" if any(c["status"] == "BLOCKED" for c in checks) else "PASS")


def analyze_records(records: list[dict], *, evidence_kind: str = "unknown",
                    target: str = "unconfirmed", expected_firmware: str = EXPECTED_FIRMWARE) -> dict:
    checks: list[dict] = []
    if any(isinstance(r.get("data"), dict) and isinstance(r["data"].get("context"), dict)
           and r["data"]["context"].get("synthetic") is True for r in records):
        evidence_kind = "synthetic"
    def check(name, status, detail, **measurements):
        checks.append({"name": name, "status": status, "detail": detail, "measurements": measurements})
    report = {"schema_version": SCHEMA_VERSION, "scope": "offline_torque_off_capture_acceptance",
              "target": target, "declared_evidence_kind": evidence_kind,
              "physical_execution": False, "physical_safety_certified": False,
              "usb_transport_independently_verified": False, "motion_authorized": False,
              "absolute_feedback_age_verified": False, "driver_queue_latency_measured": False,
              "limits": {"warmup_frames": WARMUP, "minimum_frames": MIN_FRAMES,
                         "rate_hz": [49.5, 50.5], "sample_p99_ms_max": 25,
                         "sample_max_ms": 40, "feedback_handling_age_ms_max": 100,
                         "ack_latency_ms_max": 120, "current_complete_fraction_min": .99},
              "checks": checks, "limitations": [
                  "PASS only describes supplied records; capture provenance is declared, not authenticated.",
                  "Missing ticks infer absent recorded feedback, not where or why USB bytes were lost.",
                  "Firmware and host clocks differ: absolute sensor-to-host age cannot be inferred.",
                  "Driver callback timestamps exclude unknown UART/USB and SerialInputOutputManager queue delay.",
                  "A recorded TX is queue admission/captured host output, never proof of USB write or firmware receipt.",
                  "Version strings do not attest the flashed firmware binary or physical torque registers.",
                  "Torque-off monitoring excludes powered motion, camera load, and 30-minute endurance."]}
    check("target", "PASS" if target == "dog12" else "BLOCKED",
          "Only dog12 servo IDs 1..12 are supported; confirm the actual body before using this gate.")
    check("evidence_origin", "PASS" if evidence_kind == "operator-capture" else "BLOCKED",
          "Operator-declared captured evidence." if evidence_kind == "operator-capture" else
          "Synthetic or unknown-origin input cannot establish physical USB health.")
    invalid = []
    session = records[0].get("session_id") if records else None
    times = []
    for index, record in enumerate(records):
        if (record.get("record_index") != index or type(record.get("record_index")) is not int
            or not isinstance(session, str) or not session or record.get("session_id") != session
            or not isinstance(record.get("type"), str)
            or not isinstance(record.get("data"), dict)
            or not uint(record.get("host_monotonic_ns"), 2**63-1)):
            invalid.append(index)
        else:
            times.append(record["host_monotonic_ns"])
    if any(b < a for a, b in zip(times, times[1:])): invalid.append("host clock regressed")
    if not records:
        check("capture_integrity", "BLOCKED", "No records supplied.")
    elif invalid:
        check("capture_integrity", "FAIL", "Invalid envelope/index/session/monotonic clock.", invalid_records=invalid[:20])
    elif (records[0].get("type") != "session_start" or (type(records[0]["data"].get("schema_version")) is not int or records[0]["data"].get("schema_version") != 1)
          or sum(r.get("type") == "session_start" for r in records) != 1
          or sum(r.get("type") == "session_end" for r in records) != 1
          or records[-1].get("type") != "session_end"):
        check("capture_integrity", "BLOCKED", "One complete schema-1 session_start/session_end is required.")
    else:
        end = records[-1]["data"]
        ok = end.get("complete") is True and type(end.get("dropped_records")) is int and end["dropped_records"] == 0
        failed = end.get("complete") is False or (uint(end.get("dropped_records")) and end["dropped_records"] > 0) or end.get("outcome") in ("failed", "error")
        check("capture_integrity", "FAIL" if failed else "PASS" if ok else "BLOCKED",
              "Requires complete=true and dropped_records=0; recorder completeness is not byte completeness.", records=len(records))
    if invalid:
        report["status"] = status_of(checks)
        return report
    rx, tx = [], []
    decoders = {"rx": WireDecoder(), "tx": WireDecoder(allow_initial_abort=True)}
    modes = {"rx": set(), "tx": set()}
    chunk_errors = []
    startup_invalid, startup_bytes, startup_start_ns = 0, 0, None
    authenticated_abort = False
    authenticated_abort_position = None
    first_tx_delimiter = None
    startup_window_ms = None
    startup_bytes_at_ack = None
    for record in records:
        kind, data, ns = record.get("type"), record.get("data", {}), record.get("host_monotonic_ns", 0)
        if kind in {"robot_rx", "robot_tx"}:
            direction = kind[-2:]
            modes[direction].add("parsed")
            message = data.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("type" if direction == "rx" else "cmd"), str):
                chunk_errors.append("parsed message missing/object invalid")
                continue
            (rx if direction == "rx" else tx).append((message, ns, None))
        elif kind in {"usb_rx_chunk", "usb_tx_chunk"}:
            direction = "rx" if kind == "usb_rx_chunk" else "tx"
            modes[direction].add("raw")
            value = data.get("hex")
            try:
                if not isinstance(value, str) or not value or len(value) % 2 or any(c not in "0123456789abcdefABCDEF" for c in value):
                    raise ValueError("hex must be nonempty byte pairs without whitespace")
                chunk = bytes.fromhex(value)
                received = data.get("received_monotonic_ns")
                if received is not None and (not uint(received, 2**63-1) or received > ns):
                    raise ValueError("invalid callback timestamp")
                callback_ns = ns if received is None else received
                if direction == "tx" and first_tx_delimiter is None and b"\n" in chunk:
                    first_tx_delimiter = (record["record_index"], chunk.index(b"\n"), callback_ns)
                decoded = decoders[direction].feed(chunk)
                if direction == "rx":
                    callback_ns = ns if received is None else received
                    if startup_start_ns is None: startup_start_ns = callback_ns
                    candidate = decoders["tx"].abort_nonce
                    pending = bytes(decoders["tx"].pending)
                    if candidate is None and decoders["tx"].abort_pending and len(pending) == 32 and all(c in b"0123456789abcdef" for c in pending):
                        candidate = pending.decode("ascii")
                    for event, value, offset in decoders["rx"].events:
                        if event == "error" and not authenticated_abort:
                            startup_invalid += 1
                        elif (event == "message" and not authenticated_abort and candidate is not None and
                              value.get("type") == "serial_abort" and value.get("version") == 2 and value.get("nonce") == candidate):
                            startup_window_ms = (callback_ns - startup_start_ns) / 1e6
                            startup_bytes_at_ack = startup_bytes + offset
                            authenticated_abort = (0 <= startup_window_ms <= 750 and startup_bytes_at_ack <= 65536 and startup_invalid <= 32)
                            if authenticated_abort:
                                authenticated_abort_position = (record["record_index"], offset - 1, callback_ns)
                    startup_bytes += len(chunk)
                for message in decoded:
                    if not isinstance(message.get("type" if direction == "rx" else "cmd"), str):
                        chunk_errors.append("wire message lacks a string type/cmd")
                        continue
                    (rx if direction == "rx" else tx).append((message, ns if received is None else received,
                                                             None if received is None else (ns-received)/1e6))
            except ValueError as error:
                chunk_errors.append(str(error))
    for decoder in decoders.values(): decoder.finish()
    tolerated_startup = startup_invalid if authenticated_abort else 0
    bad_wire = sum(d.counts[k] for d in decoders.values() for k in ("invalid_lines", "oversized_lines", "truncated_lines")) - tolerated_startup
    mixed = any(len(m) > 1 for m in modes.values())
    check("byte_framing", "FAIL" if bad_wire or chunk_errors or mixed else "PASS" if all(m == {"raw"} for m in modes.values()) else "BLOCKED",
          "Requires raw bytes in both directions; strict after fresh nonce-v2 abort ACK. At most 32 invalid startup lines within 64 KiB/750 ms before that ACK are reported as attachment fragments, not packet loss. Parsed logs cannot establish framing.",
          directions={k: dict(d.counts) for k,d in decoders.items()}, errors=chunk_errors[:20], mixed_encoding=mixed,
          pre_handshake_invalid_lines=startup_invalid, tolerated_attachment_fragments=tolerated_startup,
          post_handshake_or_unqualified_framing_errors=bad_wire,
          startup_bytes_at_ack=startup_bytes_at_ack, startup_window_ms=startup_window_ms)
    if mixed:
        report["status"] = status_of(checks)
        return report
    hellos = [(m,t) for m,t,_ in rx if m.get("type") == "hello"]
    states = [(m,t,a) for m,t,a in rx if m.get("type") in {"policy_state", "policy_monitor_state"}]
    errors = [m for m,_,_ in rx if m.get("type") == "error"]
    versions = sorted({str(m.get("version")) for m,_ in hellos})
    identity_ok = bool(hellos) and versions == [expected_firmware]
    check("firmware_identity", "PASS" if identity_ok else "FAIL" if hellos else "BLOCKED",
          "Hello version must match the operator's expected build label; binary identity remains unverified.",
          expected=expected_firmware, observed=versions)
    abort_capable = bool(hellos) and all(m.get("supportsSerialInputAbort") is True and m.get("serialInputAbortVersion") == 2 for m,_ in hellos)
    check("reconnect_abort_compatibility", "PASS" if abort_capable and versions == [EXPECTED_FIRMWARE] else "BLOCKED",
          "Current transport requires paired 0.1.18-transportguard CAN-abort framing. Older identity/capability cannot clear physical integration.")
    nonce = decoders["tx"].abort_nonce
    matching_abort = [(m,t) for m,t,_ in rx if m.get("type") == "serial_abort" and
                      m.get("version") == 2 and m.get("nonce") == nonce and nonce is not None]
    # A decoded command only identifies its terminating LF. The earlier LF that
    # releases the startup nonce must also follow the ACK. Tied timestamps need
    # distinct ordered records; no time equality can repair reversed byte order.
    ack_before_delimiter = (authenticated_abort_position is not None and first_tx_delimiter is not None and
                           authenticated_abort_position[0] < first_tx_delimiter[0] and
                           authenticated_abort_position[2] <= first_tx_delimiter[2])
    handshake_ordered = authenticated_abort and bool(matching_abort) and bool(tx) and ack_before_delimiter
    reversed_delimiter = authenticated_abort and first_tx_delimiter is not None and not ack_before_delimiter
    check("input_abort_handshake", "PASS" if nonce and handshake_ordered else "FAIL" if reversed_delimiter else "BLOCKED",
          "Requires raw initial CAN+32hex (no LF), matching v2 nonce ACK, then LF/first command. Parsed logs cannot establish handshake bytes.",
          challenge_observed=nonce is not None, matching_ack_before_first_command=handshake_ordered,
          matching_ack_before_first_delimiter=ack_before_delimiter,
          matching_ack_position=authenticated_abort_position, first_tx_delimiter_position=first_tx_delimiter)
    def safe_command(message):
        return (message.get("cmd") in SAFE_CAPTURE_COMMANDS or
                message == {"cmd": "servo_torque", "all": True, "enabled": False})
    unsafe = [m.get("cmd") for m,_,_ in tx if not safe_command(m)]
    bad_states = sum(m.get("type") != "policy_monitor_state" or m.get("armed") is not False for m,_,_ in states)
    armed_hello = any(m.get("policyArmed") is not False for m,_ in hellos)
    admitted = any(m.get("type") == "ok" and m.get("cmd") == "policy_monitor" for m,_,_ in rx)
    capabilities = bool(hellos) and all(m.get("supportsTorqueOffPolicyMonitor") is True and m.get("policyFeedbackIntervalMs") == 20 for m,_ in hellos)
    check("torque_off_monitor_scope", "FAIL" if unsafe or bad_states or armed_hello else "PASS" if admitted and capabilities and states else "BLOCKED",
          "Requires admitted monitor, explicitly unarmed states, and only diagnostic TX commands. This is firmware-reported evidence, not independent register verification.",
          unsafe_tx_commands=unsafe[:20], invalid_states=bad_states, monitor_admitted=admitted)
    check("firmware_errors", "FAIL" if errors else "PASS" if rx else "BLOCKED",
          "Any recorded firmware error fails the attempt.", count=len(errors), examples=errors[:3])
    # Serial counters must bracket the monitor sample interval and be monotonic.
    bracketed = bool(states) and len(hellos) >= 2 and hellos[0][1] <= states[0][1] and hellos[-1][1] >= states[-1][1]
    counters = [m.get("serialErrorCounts") for m,_ in hellos]
    valid_counters = bool(counters) and all(isinstance(c,list) and len(c)==4 and all(uint(v) for v in c) for c in counters)
    deltas = [b-a for a,b in zip(counters[0],counters[-1])] if valid_counters and len(counters)>=2 else None
    counter_bad = valid_counters and any(b<a for left,right in zip(counters,counters[1:]) for a,b in zip(left,right))
    fifo_bad = any(m.get("serialFifoThresholdConfigured") is False for m,_ in hellos)
    fifo_known = bool(hellos) and all(m.get("serialFifoThresholdConfigured") is True for m,_ in hellos)
    check("uart_errors", "FAIL" if counter_bad or fifo_bad or deltas and any(d != 0 for d in deltas) else "PASS" if bracketed and valid_counters and fifo_known else "BLOCKED",
          "Cumulative counters are differenced, never summed across samples; reset/regression invalidates the comparison.",
          order=["fifo_overflow", "buffer_full", "frame", "parity"], deltas=deltas, bracketed=bracketed)
    count = len(states)
    check("sample_coverage", "PASS" if count >= MIN_FRAMES else "BLOCKED",
          "At least 1,250 monitor frames are required; first 50 warm up timing only.", frames=count, warmup_frames=WARMUP)
    ticks = [m.get("tick") for m,_,_ in states]
    valid_ticks = bool(ticks) and all(uint(t) and t > 0 for t in ticks)
    missing = ticks[0]-1 + sum(max(0,b-a-1) for a,b in zip(ticks,ticks[1:])) if valid_ticks else None
    regressed = sum(b<=a for a,b in zip(ticks,ticks[1:])) if valid_ticks else None
    check("feedback_sequence", "FAIL" if count and (not valid_ticks or missing or regressed) else "PASS" if valid_ticks else "BLOCKED",
          "Tick gaps count absent recorded feedback, not USB-packet loss attribution.",
          inferred_missing_feedback_messages=missing, duplicate_or_regressed_ticks=regressed,
          inferred_loss_fraction=missing/(count+missing) if missing is not None and count else None)
    missed = [m.get("missed_feedback_periods") for m,_,_ in states]
    missed_valid = bool(missed) and all(uint(v) for v in missed)
    missed_total = missed[-1] if missed_valid else None
    missed_bad = missed_valid and (missed_total != 0 or any(b<a for a,b in zip(missed,missed[1:])))
    check("firmware_deadlines", "FAIL" if missed_bad else "PASS" if missed_valid else "BLOCKED",
          "Monitor admission resets this cumulative counter; last value is missed periods, not the sum.", missed_periods=missed_total)
    measured = states[WARMUP:]
    samples = [m.get("sample_ms") for m,_,_ in states]
    valid_samples = bool(samples) and all(uint(s) for s in samples)
    all_intervals = [((b-a)&UINT32) for a,b in zip(samples,samples[1:])] if valid_samples else []
    sample_intervals = all_intervals[max(0,WARMUP-1):]
    host_intervals = [(b[1]-a[1])/1e6 for a,b in zip(states[max(0,WARMUP-1):], states[WARMUP:])]
    def hz(intervals):
        return 1000*len(intervals)/sum(intervals) if intervals and sum(intervals)>0 else None
    shz, hhz = hz(sample_intervals), hz(host_intervals)
    sample_stats, host_stats = stats(sample_intervals), stats(host_intervals)
    timing_bad = (bool(states) and not valid_samples) or any(v<=0 or v>40 for v in all_intervals) or any(v<0 for v in host_intervals)
    if sample_intervals:
        timing_bad = timing_bad or not (49.5<=shz<=50.5) or not (hhz is not None and 49.5<=hhz<=50.5) or sample_stats["p99"]>25
    check("feedback_timing", "FAIL" if timing_bad else "PASS" if len(measured)>=1200 else "BLOCKED",
          "Firmware uint32 millis wrap is supported; clock regressions/reset and startup stalls fail. Host rate is capture timing, not hardware latency.",
          firmware_hz=shz, host_hz=hhz, firmware_interval_ms=sample_stats, host_interval_ms=host_stats)
    ages = [a for _,_,a in states if a is not None]
    check("feedback_handling_age", "FAIL" if any(a>100 for a in ages) else "PASS" if count and len(ages)==count else "BLOCKED",
          "Same-host driver-observer callback to recorder-admission delay only, excluding unmeasured UART/USB/driver queue latency. This does not establish physical feedback age. Pixel parsed logs lack callback timestamps.",
          callback_to_recorder_admission_ms=stats(ages), absolute_sensor_to_host_age_ms=None)
    drift = []
    if valid_samples and states:
        elapsed_ms = 0
        drift.append(0.0)
        for index, interval in enumerate(all_intervals, start=1):
            elapsed_ms += interval
            drift.append((states[index][1] - states[0][1]) / 1e6 - elapsed_ms)
    drift_range = max(drift)-min(drift) if drift else None
    check("sample_to_callback_drift", "FAIL" if drift_range is not None and drift_range > 100 else "PASS" if len(drift)>=MIN_FRAMES else "BLOCKED",
          "Relative host timestamp progress minus firmware sample progress, with arbitrary starting offset removed. A >100 ms range reveals added delay or clock trouble; a small range cannot exclude constant pre-existing latency.",
          relative_progress_difference_ms=stats(drift), range_ms=drift_range,
          host_timestamp_basis="driver_observer_callback" if modes["rx"]=={"raw"} and len(ages)==count else "record_timestamp",
          absolute_sensor_age_ms=None)
    frame_commands = [(m,t) for m,t,_ in tx if m.get("cmd") == "policy_monitor_frame"]
    seqs = [m.get("seq") for m,_ in frame_commands]
    tx_valid = bool(seqs) and all(uint(s) and s>0 for s in seqs)
    target_bad = any(not isinstance(m.get("targets"),dict) or set(m.get("targets",{})) != {str(i) for i in IDS} or not all(number(v) for v in m.get("targets",{}).values()) for m,_ in frame_commands)
    seq_bad = bool(seqs) and (not tx_valid or target_bad or any(b<=a for a,b in zip(seqs,seqs[1:])))
    sent = {m["seq"]: t for m,t in frame_commands} if tx_valid else {}
    acks = [m.get("seq") for m,_,_ in states]
    ack_valid = bool(acks) and all(uint(v) for v in acks)
    latency, seen, unknown, future, stalled = [], set(), 0, 0, 0
    ack_lags, outstanding_ages = [], []
    sent_times = [t for _,t in frame_commands]
    if ack_valid:
        for (m,t,_) in states:
            seq = m["seq"]
            if tx_valid and not seq_bad:
                latest_sent_index = bisect_right(sent_times, t) - 1
                if latest_sent_index >= 0:
                    ack_lags.append(max(0, seqs[latest_sent_index] - seq))
                next_index = bisect_right(seqs, seq)
                if next_index < len(seqs) and t >= sent[seqs[next_index]]:
                    age_ms = (t - sent[seqs[next_index]]) / 1e6
                    outstanding_ages.append(age_ms)
                    if age_ms > 120: stalled += 1
            if seq == 0: continue
            if seq not in sent: unknown += 1; continue
            if sent[seq] > t: future += 1; continue
            if seq not in seen: latency.append((t-sent[seq])/1e6); seen.add(seq)
    ack_bad = (count and not ack_valid) or unknown or future or stalled or (ack_valid and any(b<a for a,b in zip(acks,acks[1:]))) or any(v>120 for v in latency)
    repeated_acks = sum(b==a for a,b in zip(acks,acks[1:])) if ack_valid else None
    longest_run, run_length = 0, 0
    previous_ack = None
    if ack_valid:
        for ack in acks:
            run_length = run_length + 1 if ack == previous_ack else 1
            longest_run = max(longest_run, run_length)
            previous_ack = ack
    final_acked = bool(seqs) and ack_valid and acks[-1] == seqs[-1]
    check("command_acknowledgement", "FAIL" if seq_bad or ack_bad else "PASS" if tx_valid and final_acked else "BLOCKED",
          "TX timestamp is recorder/queue admission, not confirmed native write. seq is latest accepted command, not an acknowledgement per command; unobserved intermediate seqs are not proven packet loss. Final sent seq must appear.",
          commands=len(seqs), distinct_observed_acks=len(seen), unobserved_ack_sequences=len(sent)-len(seen),
          unknown_ack_count=unknown, ack_before_tx_count=future, stalled_ack_frames=stalled,
          repeated_ack_count=repeated_acks, longest_same_ack_run_frames=longest_run,
          latest_sent_minus_ack=stats(ack_lags), oldest_unacknowledged_record_age_ms=stats(outstanding_ages), final_sequence_acknowledged=final_acked,
          tx_record_to_first_ack_ms=stats(latency))
    invalid_servo = 0
    incomplete_servo = 0
    incomplete_current = 0
    current_known = 0
    per_servo = {str(i): {"angles_deg": [], "current_raw": []} for i in sorted(IDS)}
    imu_bad, imu_missing, norms, gyros = 0, 0, [], []
    processing = {k: [] for k in ("feedback_us", "current_us", "frame_us")}
    for message,_,_ in states:
        ids, angles, statuses = message.get("ids"), message.get("angles_deg"), message.get("status_errors")
        shape = isinstance(ids,list) and len(ids)==12 and all(type(i) is int for i in ids) and set(ids)==IDS and isinstance(angles,list) and len(angles)==12 and isinstance(statuses,list) and len(statuses)==12
        if not shape: invalid_servo += 1
        elif not all(number(v) for v in angles) or not all(type(v) is int and v==0 for v in statuses): invalid_servo += 1
        if message.get("feedback_complete") is not True: incomplete_servo += 1
        current = message.get("current_raw")
        cvalid = isinstance(current,list) and len(current)==12 and all(type(v) is int and -32768<=v<=32767 for v in current)
        if message.get("current_complete") is True and cvalid: current_known += 1
        else: incomplete_current += 1
        if shape:
            for i, angle in zip(ids,angles):
                if number(angle): per_servo[str(i)]["angles_deg"].append(float(angle))
            if isinstance(current,list) and len(current)==12:
                for i,value in zip(ids,current):
                    if type(value) is int: per_servo[str(i)]["current_raw"].append(value)
        accel, gyro = message.get("accel_mg"), message.get("gyro_dps")
        if not isinstance(accel,list) or len(accel)!=3 or not isinstance(gyro,list) or len(gyro)!=3: imu_missing += 1
        elif not all(number(v) for v in accel+gyro): imu_bad += 1
        else:
            norm = math.hypot(*accel)
            if math.isfinite(norm): norms.append(norm)
            else: imu_bad += 1
            gyros.extend(float(v) for v in gyro)
        for key in processing:
            value = message.get(key)
            if uint(value): processing[key].append(value)
    check("servo_feedback", "FAIL" if invalid_servo or incomplete_servo else "PASS" if count else "BLOCKED",
          "Requires unique dog12 IDs, all twelve finite angles, complete feedback, and zero status errors on every frame.",
          invalid_frames=invalid_servo, incomplete_frames=incomplete_servo,
          servos={i:{key:stats(v) for key,v in values.items()} for i,values in per_servo.items()})
    current_fraction = current_known/count if count else None
    check("servo_current", "PASS" if current_fraction is not None and current_fraction>=.99 else "FAIL" if count else "BLOCKED",
          "Independent non-fatal firmware current reads; missing values stay missing, never zero. This acceptance check requires 99% complete frames.",
          complete_fraction=current_fraction, incomplete_frames=incomplete_current, current_step_ma=6.5)
    imu_available = bool(hellos) and all(isinstance(m.get("imu"),dict) and m["imu"].get("available") is True for m,_ in hellos)
    gravity_bad = any(not 500<=n<=1500 for n in norms)
    check("imu", "FAIL" if imu_bad or gravity_bad else "PASS" if count and not imu_missing and imu_available else "BLOCKED",
          "Stationary torque-off plausibility: finite 3-axis readings and acceleration norm 500..1500 mg. Calibration, bias, orientation and real motion remain unverified.",
          missing_frames=imu_missing, invalid_frames=imu_bad, acceleration_norm_mg=stats(norms), gyro_axis_dps=stats(gyros))
    processing_complete = count and all(len(v)==count for v in processing.values())
    check("firmware_processing", "FAIL" if any(v>=20000 for v in processing["frame_us"]) else "PASS" if processing_complete else "BLOCKED",
          "Firmware processing frame must be below the 20 ms period; these counters exclude USB receive and host inference.",
          **{k:stats(v) for k,v in processing.items()})
    teardown = records[-1].get("data", {}).get("transport_teardown") if records else None
    teardown_known = isinstance(teardown, dict) and teardown.get("quiesced") is True and teardown.get("host_write_outcome") == "HOST_WRITE_COMPLETED"
    teardown_failed = isinstance(teardown, dict) and (teardown.get("quiesced") is False or teardown.get("host_write_outcome") in ("WRITE_FAILED", "TIMED_OUT", "WRITE_TIMED_OUT", "SAFETY_DEADLINE_EXCEEDED"))
    check("transport_teardown", "FAIL" if teardown_failed else "PASS" if teardown_known else "BLOCKED",
          "Requires independently recorded closeAsync outcome and quiesced completion. close() returning or session_end alone does not establish this; native host completion is not firmware receipt or physical torque-off.")
    report["status"] = status_of(checks)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path, help="explicit regular JSONL file; no devices, ZIP, stdin, or networking")
    parser.add_argument("--target", choices=("dog12", "rover", "unconfirmed"), default="unconfirmed")
    parser.add_argument("--evidence-kind", choices=("operator-capture", "synthetic", "unknown"), default="unknown")
    parser.add_argument("--expected-firmware", default=EXPECTED_FIRMWARE)
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)
    try:
        records, source = read_capture(args.capture)
        report = analyze_records(records, target=args.target, evidence_kind=args.evidence_kind,
                                 expected_firmware=args.expected_firmware)
        report["source"] = source
        unfinished_name = args.capture.name.endswith(".partial") or ".interrupted." in args.capture.name
        report["checks"].append({"name":"capture_file_finalization", "status":"BLOCKED" if unfinished_name else "PASS",
            "detail":"Partial/interrupted capture paths cannot establish successful recorder finalization.", "measurements":{}})
        report["status"] = status_of(report["checks"])
    except FileNotFoundError as error:
        report = {"schema_version": 1, "status": "BLOCKED", "physical_execution": False,
                  "usb_transport_independently_verified": False, "error": str(error)}
    except (OSError, ValueError, RecursionError) as error:
        report = {"schema_version": 1, "status": "FAIL", "physical_execution": False,
                  "usb_transport_independently_verified": False, "error": str(error)}
    if args.format == "json":
        print(json.dumps(report, indent=2, allow_nan=False))
    else:
        print(f"{report['status']}: offline torque-off capture acceptance; no device accessed")
        for check in report.get("checks", []): print(f"{check['status']} {check['name']}: {check['detail']}")
        if "error" in report: print(report["error"])
    return EXIT[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
