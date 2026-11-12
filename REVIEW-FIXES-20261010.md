# Independent review corrections

These corrections follow review of `ad13173b25d0f034d74b984a69a284d2a223c6e9`.
The branch remains `review/robot-v5-reconciled`. No package was published or
Library item replaced. The prior reports and build log remain intact.

## Provider admission

The old physical authority claimed publication before decoding and only
rechecked its ten-second authority deadline afterward. Four new deterministic
Kotlin regressions fail against the old source: decoding crossing eight seconds,
the claim callback crossing the exact cutoff, malformed input claiming
publication, and replacement during a reentrant claim restoring an old lease.

The corrected path decodes and validates first. It checks the eight-second
cutoff before claiming publication, then checks the request object, generation,
operation identity, both deadlines and remaining command duration again after
the claim callback. Cancelled or replaced work cannot revoke or publish over a
replacement request. No timer or wall-clock sleep is needed for these regressions.

## Startup capture ordering

The old acceptance validator compared ACK time with the first decoded command
but omitted the earlier delimiter that terminates startup. An authored reversed
delimiter/ACK fixture passed both its handshake and overall acceptance.

The corrected validator records the first raw TX LF byte's record index, offset
and callback timestamp and the authenticated ACK's corresponding position.
The ACK must be in an earlier record and have no later callback timestamp.
Equal timestamps use explicit record ordering; equality cannot repair reversed
records. A delimiter inside the challenge chunk or across fragmented challenge
chunks fails. Valid split challenges and split ACKs still pass. Missing or
unqualified evidence remains BLOCKED; proven reversed ordering is FAIL.

## Verification

All 68 hardware-acceptance unit tests passed in the existing Linux environment.
The corrected full host Kotlin suite passed 289 tests. Android passed 283 unit
tests and built debug and instrumentation APKs offline with existing tools.
No device tests ran. Complete canonical results are retained in
`review-fixes-validation`; the exact copied source proof is
`review-fixes-source-proof.json`, both in the task evidence directory outside Git.
The full canonical profile passed all 21 functional gates and two stability checks:
1,161 tests plus seven CPU subtest reports, zero failures or skips. This final
evidence paragraph and the link in V5-RECONCILIATION.md were added after testing;
production and test source bytes are unchanged.
The full old-source host reproduction ran 289 tests and failed only the four
new authority regressions as expected. The capture reproduction records old
PASS and corrected FAIL for the same reversed-order synthetic fixture.
The native-Windows capture attempt is retained with permission/symlink platform
errors; it was not counted as a pass or used to weaken the tests.

Firmware source is unchanged by these corrections. The prior 14-scenario host
replay and ESP32 binary generation evidence still applies to those bytes.
The wrapper's outer build exit was 0 because it did not propagate PlatformIO's
return. The log records binary SUCCESS followed by a blocked tool-upgrade
connectivity check. It was not a device action, and the update was not retried.
Do not treat that exit code as a clean maintenance completion.

The exact bundle remains unpromoted and transport-unverified. Independent
re-review, real provider/model qualification, paired motor-power-off installation,
physical acceptance and the separate wheeled-navigation work remain outstanding.
No push, install, provider call, training, simulation, serial access or motion
occurred for these corrections.
