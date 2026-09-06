# First unsupported floor walk, September 6

Leo reports that epoch 2750 walks freely on the floor, slowly and unbalanced.
This is the first reported unsupported walking baseline. The recording confirms
sustained policy operation and substantial body rocking; it has no external
position measurement or video to independently measure walking speed.

The deployed source is preserved on GitHub at annotated tag
`robot-first-floor-walk-20260906`, pointing to `d745de3`. The installed APK was
built from `71e96b3`. No policy, calibration, firmware or runtime settings were
changed during this analysis, and no agent initiated physical motion.

## Baseline and evidence

| Item | Identity |
| --- | --- |
| Actor | V22 epoch 2750, `current_body_v22_428`, CAD drive coordinates |
| Weights SHA256 | `95b81a0056686a9d8de82d503e0a34072c45dfa2ddd3ceccfa738c69d969ea3c` |
| APK SHA256 | `0beab9ce6e310f3d00269f81eb6edbb2da33e2a0f677930249092219d3a57e9e` |
| Calibration SHA256 | `6d5cccade24a54c8ff98e7e194f41af676c9f6268be15e44d9293b649090bfa6` |
| Firmware | 0.1.15 |
| Session | `f08fda0c-bb93-4a85-9dbc-1e2fec2a44b6` |
| Phone recording | `robot-run-20260906-163033-934-f08fda0c.jsonl` |
| Recording SHA256 | `3d8d439986d515df7000285f8625a7d57b6de6e193b2c618f91ecd54a222ccff` |

The complete JSONL and training-capture ZIP are stored outside Git under
`C:\Users\Leo\Videos\Robot-policy-review-20260905\physical-first-walk-20260906`.
The JSONL matches the phone's SHA256. Capture manifest hashes were checked,
including the recording, policy, calibration and stride reference. The session
contains 1,180 policy frames, a complete trailer and zero dropped log records.
Total recording duration is 29.157 seconds, including preparation; policy
sensor timestamps span 25.385 seconds.

`independent_analysis.py` and `independent-analysis.json` in that directory
preserve the NumPy analysis and detailed per-joint results. Reproduce it with
`py -3.13 independent_analysis.py` from the evidence directory, using the
`Robot-delivery` checkout. The standard dashboard is reproduced from the repo:

```powershell
$captureRoot = 'C:\Users\Leo\Videos\Robot-policy-review-20260905\physical-first-walk-20260906'
py -3.13 pixel_robot/tools/analyze_training_capture.py "$captureRoot\latest-training-capture.zip" --servo-id 3 --output-dir "$captureRoot\standard-analysis" --require-50hz
```

The corrected rate check must fail this capture. The dashboard remains useful
for inspecting the traces even when the gate fails.

## Measured failures and limits

Balance and tracking statistics below use the 979 frames after the stride
clock reaches four seconds, excluding the configured settle and ramp. That
window spans 21.003 physical seconds. Timing statistics cover the full policy
episode. Percentiles in the independent analysis use NumPy interpolation.

| Observation | Result | Interpretation |
| --- | --- | --- |
| Command | Constant forward 0.04 m/s; lateral and yaw zero | The trial requested only 4 cm/s. Actual translation is unmeasured. |
| Feedback rate | 46.445 Hz over the policy episode | The physical loop does not meet 50 Hz. |
| Sample intervals | Median 21 ms, p95 23 ms, maximum 37 ms | Successful samples arrive slower than the intended 20 ms schedule. |
| Missed schedule counter | Final cumulative value 90; continuous sample tick IDs | Tick continuity does not prove that all deadlines were met. Do not sum cumulative counters. |
| Inference | Median 0.657 ms, p95 0.904 ms, maximum 1.946 ms | Neural inference is well below the 20 ms budget in this run. |
| Gait clock | 23.580 s for 25.385 s of physical time | Clock progression was about 7.1% slower than wall time. |
| Estimated body tilt | Median 10.70 degrees, p95 18.37, peak 21.07 | Confirms substantial rocking after startup. This is onboard IMU estimation. |
| Estimated roll | Mean -5.99 degrees | There is a persistent lateral lean in the onboard estimate. |
| Worst joint tracking error per frame | Median 5.71 degrees, p95 11.31, maximum 13.06 | The servos do not instantaneously reach acknowledged targets. |
| Run termination | `policy frame requires exactly 12 targets` | Transport or parsing reliability remains unresolved. |

`frame_compute_ns` includes the wait for feedback, because it is recorded after
that wait. It must not be interpreted as CPU or neural inference time. Phone
thermal status remained zero. The reported 7.1 V was sampled before active
walking; it does not establish voltage under load.

### Actuator response

The unchanged August 29 response fit was used to predict measured positions
from the acknowledged targets and first measured joint positions. This was a
prediction check, not a new fit. Its SHA256 is
`fe55216db066b854df8780f645b7a2f93212c030bb735d6b64faf1015ff6854e`.

For the eight hip/knee flexion servos, position prediction RMSE is 0.76 to
1.91 degrees, versus 2.01 to 4.78 degrees between simultaneous measured and
target positions. The existing fit already explains much of the apparent lag.
This does not validate the entire loaded simulation, but it argues against
blindly replacing the fit or treating tracking error as unexplained motor delay.

Best time shifts for those flexion traces are 85 to 145 ms. These are effective
closed-loop phase lags, including loading and ground contact, not isolated
motor time constants. This 25-second run is shorter than the existing fitter's
40-second minimum; that requirement was not weakened.

About 63.3% of scalar actor outputs after startup are at the portable actor's
absolute-one bound. Every measured frame contains at least one such output.
This warrants comparison with matched simulated observations and outputs.
It does not establish an observation error or justify raising residual limits.
The stride controller's residual scale is 0.045 radians, about 2.58 degrees.

### Terminal packet

The recorded host packet immediately before the error, sequence 1180, contains
exactly twelve finite targets in calibration range. Its encoded length is
203 bytes including newline, far below the 6,144-byte receive buffer. The error
arrives approximately 10 ms later. The asynchronous pipeline also records the
next host command before observing the stop.

The recording contains the host's transmit message, not the ESP32's raw
received bytes. It cannot distinguish serial corruption from a parser or
document-state fault. Preserve the twelve-target validation and watchdog.
The smallest useful next diagnostic records received length, parsed sequence,
target count and document overflow state when validation fails. Add integrity
framing if the received-byte evidence supports that cause.

### Evaluation bug

The old offline `transport_50hz_gate` accepted this run because median and p95
intervals were within loose bounds, tick IDs were continuous, and feedback was
complete. It omitted aggregate rate and the firmware missed-period counter.
Consequently its earlier `passed: true` is not physical 50 Hz qualification.
Both analysis tools now require host and firmware aggregate rates between
49.5 and 50.5 Hz, complete timing evidence, and a valid zero missed-period
counter on every policy frame. They retain the previous checks. The actual
capture now fails for both rates and the nonzero counter. The timing plot
also labels the Android duration as a cycle including wait.

Focused validation ran 15 tests: 14 passed and one optional August 29 recording
test skipped because its local evidence file is absent. Shared fixtures check
both analyzers against valid 20 ms operation, continuous slow operation,
host/sensor timing disagreement, missing sample/counter evidence, invalid
counters and cumulative misses. The cumulative value 90 remains 90 in reports.
The real September 6 capture was reprocessed separately and fails as expected.

## Next improvement sequence

1. Preserve this exact walking bundle and recording as the rollback baseline.
   Correct the offline gate before judging subsequent changes.
2. Resolve actual scheduling and packet termination with motor-disabled
   transport measurements first. Require measured 49.5 to 50.5 Hz, complete
   ordered feedback, no missed scheduled periods during the measured window,
   and no protocol fault over a 60-second sample. Inference-only benchmarks
   cannot satisfy this requirement. Leo operates any physical walking test.
3. Reproduce the constant 0.04 m/s command and startup in simulation using the
   recorded control intervals as actual action-hold durations. Current timing
   randomization changes actor input intervals while physics still advances
   with a 20 ms control step. Compare orientation, joint histories, acknowledged
   targets, current inputs and actor clipping before changing rewards. Separate
   onboard orientation-estimator error from physical body lean where possible.
4. Use that comparison to select one balance experiment. Keep the correct CAD,
   linkage coordinates and existing response fit unless replay contradicts
   them. Do not assume stronger upright penalties or longer PPO solve the gap.
   Retain signed progress, repeated foot lift/landing and stopped support so a
   motionless or sliding policy cannot win by looking level.
5. Compare the candidate and baseline at the same slow command before increasing
   speed. Retain the deterministic 20/60-second simulation gates, timing and
   dynamics variants, and inspect full rollout videos. Then compare repeated
   user-operated floor runs with the same setup and externally measured travel.
   A candidate must reduce both median and p95 tilt without losing progress,
   stopping support or runtime reliability. A single run is preliminary.
6. Leo explicitly requests faster walking in every direction in the next
   training round. Increase flat-ground forward, backward and both sideways
   command ranges, and both yaw rates, in bounded stages once that comparison
   passes. Evaluate each signed direction separately, along with combined
   translation/turning and transitions to a supported stop. One fast forward
   result cannot qualify the full command range. Increase physical app command
   limits only with an accepted bundle covering those speeds. Start rough
   terrain at compressed height only after flat balance and command tracking
   work, then restore height gradually as previously agreed.

No new policy is promoted by this analysis. The supported conclusion is that
the robot now walks according to Leo's observation, while physical balance,
50 Hz operation and uninterrupted communication still need improvement.
