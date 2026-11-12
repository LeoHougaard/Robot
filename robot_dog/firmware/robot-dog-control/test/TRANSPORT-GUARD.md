# Transport-guard candidate: offline evidence and deployment limits

`0.1.18-transportguard` is a **source-only, unflashed candidate**. The latest
reported installed ESP32 version was `0.1.17-rxdiag`; the installed Pixel
`0.2.10` timing-tolerant path has no verified powered retry in this work.
None of these offline checks authorizes or qualifies a physical run.

## Reproduced defects and narrow fixes

The adversarial harness compiles the actual production parser/dispatcher,
policy admission/frame/feedback/watchdog, boot disarm, stop, and torque-off
code. Only device boundaries and unrelated manual-command handlers are fake.

1. **Expired watchdog could be refreshed by a late frame.** `loop()` polls
   serial before checking the watchdog. A frame arriving 121 ms after the last
   accepted frame used to write a new target and reset the deadline. Admission
   now checks the same existing `> 120 ms` limit before any target write. At
   exactly 120 ms it retains the previous behavior; after 120 ms it disarms,
   holds existing targets, and requires an explicit new arm. Unsigned timer
   wrap is covered.
2. **A valid JSON prefix could survive a lost delimiter.** ArduinoJson accepts
   the first object even when more bytes follow it. A concatenated command,
   trailing junk, or NUL could therefore apply the first target. The new
   boundary helper requires exactly one complete object, with optional JSON
   whitespace, before the unchanged JSON parser/type checks. Malformed frames
   never refresh the watchdog. Strings, escapes, nested arrays/objects, and
   normal CRLF remain supported.
3. **A bare reconnect newline could execute an old complete command.** A
   previous connection might have left a complete arm or torque-on object in
   the ESP32 input buffer, missing only LF. Sending LF on reconnect would
   execute that old command. The candidate recognizes ASCII CAN (`0x18`)
   before line parsing, clears the pending input, and discards through the next
   LF. It then collects exactly 32 lowercase hexadecimal nonce characters without
   a delimiter, and replies with `{"type":"serial_abort","version":2,"nonce":"..."}`
   only after receiving the complete challenge.

## Reconnect contract and compatibility

The updated host must send **CAN followed by a fresh 32-character lowercase
hexadecimal nonce, without LF**, and wait for the `serial_abort` version-2
acknowledgment echoing that exact nonce before sending a delimiter or command.
Buffered acknowledgments from previous connections cannot satisfy a new
challenge. After a matching acknowledgment, the first LF exits discard mode;
hello and normal commands can follow. Invalid or truncated challenges produce
no acknowledgment. A new CAN resets nonce collection. Lost CAN cannot execute
a complete old command because the request contains no delimiter.

On acknowledgment timeout or failure the host must close without sending a
newline or safety batch. Old firmware does not implement this acknowledgment
and is intentionally incompatible with the new handshake. Do not fall back
to a bare newline or overflow padding: observed UART byte loss makes a
padding-length assumption unsafe.

The candidate hello reports `supportsSerialInputAbort: true` and `serialInputAbortVersion: 2` alongside
its firmware identity. The early dedicated acknowledgment is necessary;
waiting for hello to discover capability would already have sent a delimiter.
This is a protocol resynchronization mechanism, not an authenticated session
or proof that bytes reached actual motors. A deliberate arm resets the frame
sequence to zero; the host must invalidate queued commands from old sessions.

## Safety behavior preserved

- Boot sends torque-off without moving to saved home angles
- Explicit `CALIBRATED_AND_LIFTED`, calibrated unique IDs, live synchronized
  feedback, IMU availability, and hardware status checks remain required
- Stop and policy disarm hold existing targets; they do **not** prove torque
  off. The separate `servo_torque` disable command is required, and real
  torque-off still needs hardware evidence
- Read-only monitoring still requires verified torque-off and performs no
  target writes
- Command timeout stays 120 ms, feedback interval 20 ms, USB baud 2 Mbps,
  servo baud 1 Mbps, FIFO threshold 32, RX buffer 8192 bytes, and existing
  feedback failure thresholds/calibration/policy gates are unchanged

## Useful evidence for the first user-operated test

Record the actual app version and firmware hello, including
`serialRxBufferBytes`, `serialFifoThresholdConfigured`,
`supportsSerialInputAbort`, `serialInputAbortVersion`, and the four UART error counters. Preserve complete
raw capture and timestamps so 128-byte losses, sequence/tick gaps, freshness,
and `missed_feedback_periods` can be distinguished. Validate the dedicated nonce-matching
abort acknowledgment on every newly opened transport, then verify the reported
firmware identity against the intended paired candidate. An existing installed
`0.1.17-rxdiag` is not evidence that these changes are running.

Offline success does not establish UART electrical reliability, real USB
scheduling/latency, physical stop/torque-off, stable servo power, or safe
locomotion. The user's normal lifted/secured test and physical stop procedure
remain necessary. No board connection, flashing, monitoring, motor operation,
backend workload, Android SDK terms, or credential operation occurred here.

## Current verification boundary

Native production-code and adversarial replay checks passed; the candidate
ESP32 compile remains **blocked** by pinned official-registry dependency
checksum/proxy failures and canceled approval reviews. See
`compile-only-evidence.json`. Historical 2026-10-02 compilation belongs to an
earlier source snapshot and must not be represented as a candidate build.
