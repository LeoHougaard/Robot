# Learned locomotion commissioning

The learned controller runs at 50 Hz and accepts body-frame
`forward_mps`, `lateral_mps`, and `yaw_rate_rps`. The commissioned envelope
is 0-0.18 m/s forward, zero lateral speed, and +/-0.25 rad/s yaw.
`goal_controller.py`
converts a world `x/y/yaw` target into those commands, but it requires a fresh
external pose estimate. The onboard IMU can stabilize heading; it cannot
measure world `x/y` position by itself.

The firmware transport is deliberately locked until all of these are true:

1. IDs 1-12 are unique, enabled, have non-default min/home/max limits, and
   each has a deliberate nonzero torque limit below the servo maximum.
2. Every entry in `assembly-1-12dof.calibration.json` has a measured zero,
   direction, and safe range; both calibration flags are `true`.
3. The robot is lifted or supported, torque is reduced, and the operator sends
   the exact `CALIBRATED_AND_LIFTED` arm confirmation.
4. The exported policy metadata identifies profile SHA `B25A4A05...78035D3`
   and contains a passing deterministic Goal result. The runner rejects the
   historical `615092...` rear-knee profile.

Trials remain lifted at the current commissioning stage. The firmware rejects missing targets,
out-of-limit targets, target jumps above 6 degrees per 20 ms frame, stale
sequence numbers, lost feedback, and host pauses over 120 ms. Disarming holds
the last position rather than dropping a loaded leg. CLI commissioning uses
`--torque-off-on-exit`, and UI trials always turn torque off after normal
completion, Stop + Disarm, or an error.

The physical knee servos drive the lower links through four-bar linkages. The
four knee entries therefore declare `four_bar_follow`: the runtime maps each
simulated relative knee angle plus its leg's hip-flexion angle to the absolute
lower-link servo angle. Feedback uses the inverse mapping before it enters the
policy observation. This is the same geometry convention used by the browser
UI's Whole Robot Walk Test. Keep the linkage entries in the calibration file;
measure direction and scale at the servo, and do not compensate for the
linkage a second time in the weights or firmware.

Training joint order and physical numbering are intentionally different. The
runtime requires this exact semantic translation and rejects every other
permutation:

| Simulation leg | Robot servo IDs | Diagonal |
| --- | --- | --- |
| Front right (FR) | 7, 8, 9 | FR + BL |
| Front left (FL) | 1, 2, 3 | FL + BR |
| Back right (BR) | 4, 5, 6 | FL + BR |
| Back left (BL) | 10, 11, 12 | FR + BL |

Synchronized feedback is keyed by physical servo ID, reordered to simulation
FR/FL/BR/BL joint order, converted from measured degrees to signed policy
radians, and inverse-transformed through the four-bar mapping. A missing servo
feedback bit disarms the transport rather than reusing a commanded angle.

Run the pure goal-controller tests from this directory:

```powershell
python -m unittest -v test_goal_controller.py test_policy_runtime.py
```

The measured assembly calibration is installed. While the robot remains
supported, run a stationary commissioning trial with explicit torque-off:

```powershell
python run_policy.py --port COM3 --duration 0.5 --confirm-lifted --torque-off-on-exit
```

Then use a small command, still lifted, such as `--forward 0.05` or
`--yaw-rate 0.10`. Do not start floor testing until joint directions, limits,
feedback, IMU axes, watchdog disarming, and lifted motion are all verified.

For the browser controls, start the localhost-only bridge and leave its
PowerShell window open:

```powershell
python run_policy.py --ui --port COM3
```

Open the ESP32 robot UI at `http://10.1.39.32`, use the **Remote Control** panel to connect to
`http://127.0.0.1:18765`, and disconnect the UI's manual board connection before
driving. Connecting the runner now automatically releases the direct
board transport, and loading the page prefers the local runner instead of
auto-connecting Wi-Fi. Pressing **Start Remote Control** is the explicit browser
motion request; there is no second lifted-confirmation checkbox. Speed and yaw
changes are sent live until **Stop + Disarm**. A two-second command heartbeat
disarms if the browser disappears. The browser
never sends policy frames to the ESP32 itself; inference, calibration, limits,
serial ownership, disarming, and the 50 Hz loop remain in `run_policy.py`.

The **Drive torque** slider selects the limit applied to all 12 servos before
remote control arms. It defaults to 100% and accepts 1–100%. The selected limit does
not change the policy weights, joint mapping, or command envelope, and every UI
exit path still disables physical torque.

If the robot is resting or laid down, **Stand to Neutral** first reads all 12
joint angles and interpolates to the saved neutral centers in increments no
larger than 3 degrees. It uses the selected torque and holds the verified
neutral pose for remote control. **Stop + Disarm** disables and
physically verifies torque. Connecting the local runner automatically releases
the page's direct Wi-Fi or USB transport so the buttons are not greyed by two
simultaneous control paths.

Dynamic accelerometer vectors are normalized without a stationary magnitude
gate. A finite nonzero IMU vector is still required so policy observations stay
numerically valid.

The normal UI sequence is:

1. While supported, use the existing direct board connection and **Set Neutral**
   to establish the saved `><` center pose at reduced torque.
2. Disconnect that direct board connection so only `run_policy.py` can own
   COM3.
3. Connect the Remote Control panel to the local runner, choose drive torque,
   press **Start Remote Control**, and adjust speed/steering live.
4. Use **Zero Drive Command** to request stationary control without disarming,
   or **Stop + Disarm** to end control. Stop and errors request torque-off.

Servo centers use the existing **Whole Robot Walk Test** workflow. Adjust the
per-leg Hip/Femur/Knee center fields, click **Set Neutral** while the robot is
supported to verify the pose, then click **Apply Centers to Learned Policy**
after connecting the local runner. The effective center is the same servo home
plus Hip/Femur/Knee trim used by Set Neutral. The bridge writes those 12 values
to `zero_deg` by servo ID and shifts each joint's min/max by the same amount,
preserving its calibrated travel. Direction/scale, four-bar linkage, IMU data,
and verified flags remain unchanged. The four knee centers remain physical
lower-link servo centers; `run_policy.py` applies the four-bar conversion.
Center trims accept -45 to +45 degrees. **Live Centers** can be enabled to
coalesce typed edits and send the complete synchronized 12-servo neutral pose;
the dedicated center-pose transport uses the servo's full 0-360 coordinate
instead of the older narrow walk-test travel range.

The interface retains live servo/IMU graphs, center trims, leg mapping, Leg IK,
Whole Robot Walk Test, and the lower diagnostics for debugging. The
**IMU/body yaw alignment** number, slider, and presets update the policy's
body-from-sensor yaw matrix for any angle from -180 to +180 degrees when the
board is remounted around its vertical axis; gyro bias and gravity sign are
preserved.

## Current live validation

With the robot securely supported and the 7.4 V servo rail active:

- all 12 servos returned synchronized encoder feedback with status `0`;
- physical torque limit registers were `250` (25%);
- a 50-frame exact-position stream sustained 50 Hz with 50/50 contiguous
  feedback replies, maximum one-frame host lag, and 8.47 ms average firmware
  processing;
- learned stationary motion completed for 0.5 seconds;
- learned forward motion completed at 0.03 m/s for 0.75 seconds and 0.05 m/s
  for 1.0 second;
- every completed/error path used explicit torque-off, and all 12 physical
  torque-enable registers subsequently read `0`.

This evidence is lifted-only. Keep the robot supported; do not floor-test yet.
Repeat longer lifted forward/yaw trials and verify observed linkage motion,
feedback-loss handling, emergency stop access, and safe mechanical clearance
before carrying load.

See `INTEGRATION-HANDOFF.md` for exact provenance, known limits, and the next
thread's integration checklist.
