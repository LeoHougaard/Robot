# Robot integration audit

Audited on 2026-08-15 from the consolidated repository:

- `C:\Users\Leo\Code Projects\Robot\robot_dog`
- `C:\Users\Leo\Code Projects\Robot`

No walking behavior or training source was changed.

## ESP32 and actuator hardware

The active firmware is `robot_dog/firmware/robot-dog-control`. PlatformIO uses
the Arduino `esp32dev` target. Windows' retained Plug-and-Play record for the
commissioned COM3 device identifies a Silicon Labs CP210x USB-to-UART bridge:
VID `10C4`, PID `EA60`.

The controller drives 12 ST3215/ST-series serial-bus servos at 1,000,000 baud
on ESP32 UART1 (TX 19, RX 18). The onboard QMI8658 IMU uses I2C pins 32/33.
Servos are powered from the separate servo rail; USB-C is data/controller power,
not servo power.

## Existing host protocol

The firmware already provides the safe foundation the Android app needs:

- host link at 921600 baud;
- newline-delimited JSON input and whitespace-padded CRLF JSON output;
- `hello` handshake after USB open/reset;
- `policy_arm` gated by exact confirmation, calibrated limits, 12 unique
  enabled servo IDs, synchronized feedback, clean servo status, and live IMU;
- `policy_frame` with strictly increasing sequence and all 12 absolute-degree
  targets;
- rejection of non-finite, missing, out-of-limit, or >6 degree step targets;
- synchronized servo position/speed plus IMU feedback in `policy_state`;
- disarm after three incomplete feedback frames;
- independent stale-frame disarm after 120 ms.

Firmware disarm holds the last safe target. It intentionally does not drop
torque because an uncontrolled collapse can be less safe. A deliberate
torque-off command remains available for suspended commissioning and shutdown.

## Promoted policy

The selected policy is an Isaac Lab/RL-Games PPO actor, not a Stable-Baselines
policy. `robot_dog/policy_runtime` contains the promoted portable bundle and
measured calibration.

Actor topology and preprocessing:

1. normalize 180 float observations with saved running mean/variance;
2. clip normalized input to [-5, 5];
3. three fully-connected 128-unit ELU layers;
4. 12-unit output clipped to [-1, 1].

Each 45-value frame is, in order: body angular velocity (3), projected gravity
(3), smoothed forward/lateral/yaw command (3), joint position (12),
`0.05 * joint velocity` (12), and previous applied action (12). Four frames are
flattened oldest-to-newest. Actions are limited to a 0.30 change per 20 ms
frame, then scaled by 0.25 rad.

Policy joint order is FR, FL, BR, BL. Physical servo IDs are
`7,8,9,1,2,3,4,5,6,10,11,12`. Each knee servo follows its femur with a 1:1
four-bar transmission. The measured zeros, directions, safe ranges, IMU axis
matrix, gyro bias, and gravity sign are copied unchanged into the Android app.

## Android boundary

The Android app owns USB lifecycle/reconnect, the deterministic actor loop,
logging, camera preview/latest-frame analysis, thermal reporting, and local or
remote motion requests. It never emits servo pulses. The ESP32 remains the
authority for actuator limits, feedback validation, sequence validation, and
loss-of-host response.
