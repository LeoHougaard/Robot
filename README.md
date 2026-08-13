# Robot Dog Control Starter

This is a Wi-Fi and USB-C test rig for the early robot dog platform.

It gives you:

- A browser control UI in `ui/index.html` using either Wi-Fi HTTP or the Web Serial API over USB-C.
- A copy of that UI served directly by the ESP32 at its Wi-Fi IP address.
- ESP32 firmware in `firmware/robot-dog-control` using PlatformIO.
- JSON-line commands for manual servo position movement, velocity movement, home, stop, config, and simple program playback.
- Wi-Fi setup through a captive portal and Arduino OTA support for later wireless updates.
- A learned-policy Remote Control panel backed by the localhost-only `policy_runtime/run_policy.py` bridge.

The current firmware is written for serial bus servos such as the Waveshare/Feetech-style ST series used by many Waveshare robot boards. If your exact Waveshare General Driver board already has its own firmware or uses a different servo protocol, keep the UI and adapt only `ServoBusDriver` in `src/main.cpp`.

## Quick Start

1. Install PlatformIO.
2. Connect the Waveshare ESP32 driver board over USB-C.
3. From `firmware/robot-dog-control`, build and upload:

   ```powershell
   pio run -t upload
   pio device monitor -b 460800
   ```

4. Open `ui/index.html` in Chrome or Edge.
5. Use **Connect Wi-Fi** with the board URL, or click **USB** and choose the ESP32 serial port.

After the filesystem image is uploaded, you can also open the UI directly from the board:

```text
http://10.1.39.32/
```

The first Wi-Fi boot starts a captive portal named `RobotDog-Setup` for 45 seconds. After Wi-Fi is configured, OTA is available on the network with hostname `robot-dog-control`, and the web UI is available from the board's IP address.

Wi-Fi can also be configured over USB serial:

```json
{"cmd":"wifi_set","ssid":"YourNetwork","password":"YourPassword"}
```

For learned-policy commissioning, finish the measured calibration described in
`policy_runtime/README.md`, disconnect the UI's manual board connection, and
start the local runtime bridge:

```powershell
python policy_runtime\run_policy.py --ui --port COM5
```

Then connect the **Remote Control** panel to `http://127.0.0.1:18765`. Stand to
Neutral once, press **Start Remote Control**, and adjust speed and steering live.
The panel cannot bypass the calibration flags, command envelope, neutral pose, or
firmware safety checks. Policy inference and ESP32 serial control stay inside
`run_policy.py`.

## Board Pin Setup

The servo UART pins are defined in [platformio.ini](firmware/robot-dog-control/platformio.ini):

```ini
-D SERVO_TX_PIN=19
-D SERVO_RX_PIN=18
  -D SERVO_BAUD=1000000
  -D IMU_SDA_PIN=32
  -D IMU_SCL_PIN=33
  -D MOTOR_A_PWM_PIN=25
  -D MOTOR_A_IN1_PIN=21
  -D MOTOR_A_IN2_PIN=17
  -D MOTOR_B_PWM_PIN=26
  -D MOTOR_B_IN1_PIN=22
  -D MOTOR_B_IN2_PIN=23
```

These match Waveshare's General Driver ST3215 demo pins: `S_TXD=19`, `S_RXD=18`. If the board firmware exposes a different built-in servo API, keep the UI and command protocol, then adapt `ServoBusDriver::moveTo()` in [main.cpp](firmware/robot-dog-control/src/main.cpp).

The IMU pins match Waveshare's 9DOF demo: `SDA=32`, `SCL=33`. The DC motor pins match the Waveshare TB6612 demo: motor A uses `PWMA=25`, `AIN1=21`, `AIN2=17`; motor B uses `PWMB=26`, `BIN1=22`, `BIN2=23`.

## Default Servo Setup

The firmware starts with two servos:

| ID | Name | Range | Home |
| --- | --- | --- | --- |
| 1 | Left front hip swing | 0-360 degrees | 180 |
| 2 | Left front femur | 0-360 degrees | 180 |

The **Whole Robot Walk Test** panel in the web UI uses the 12-servo leg map: left front `1,2,3`, right back `4,5,6`, right front `7,8,9`, and left back `10,11,12`. It can run all legs or one selected leg for bench testing. It will not add, rename, or enable servos from the walk controls; configure and save the IDs you want to test first. Step height defaults to `0 mm`, so the walk loop does not start moving until you raise it and click **Start Walk**. Neutral X defaults to `-20 mm` and moves the neutral IK foot target and the generated foot path together. Right-side legs use mirrored servo directions, and back legs apply the backward-mounted direction so they walk backward while keeping the same body-frame foot path. Each joint center trim accepts `-45` to `+45` degrees. **Live Centers** coalesces edits and synchronously updates the complete 12-servo neutral pose while values are typed.

The walk IK already treats each knee servo as an absolute lower-link drive for
the robot's four-bar linkage. Learned-policy deployment uses the same mapping:
the runtime combines hip-flexion and relative knee policy angles on output and
removes the hip contribution from knee feedback on input.

You can add, rename, disable, and change limits in the UI. The Leg IK panel has a guided endpoint setup: choose a setup servo, start setup, jog to the min endpoint, click **Set Min Here**, jog to the max endpoint, click **Set Max Here**, then save. After saving, normal moves are clamped to those endpoints. Each manual servo card can cycle through **Position Mode**, **Joint Velocity Mode**, and real **Motor Mode**. Motor Mode is continuous spin, so lift the robot or remove horns before testing it.

If multiple servos move together when you command only one, they share the same ST-series bus ID. Click **Add Servo**, select which physical joint it is, then click **Program** with only one unassigned servo connected. The UI programs the old ID to a new unique ID, wiggles it, and saves that joint into the manual controls. The ID command follows Waveshare's EEPROM sequence: unlock, write `SMS_STS_ID`, lock at the new ID, then ping the new ID.

## Serial Protocol

Every command is one JSON object. Over 460800-baud serial it is followed by a newline; over Wi-Fi it is sent as the body of `POST /api/command`.

Examples:

```json
{"cmd":"hello"}
{"cmd":"move","id":1,"angle":130,"speed":900,"accel":50}
{"cmd":"servo_velocity","id":1,"dps":45}
{"cmd":"servo_velocity_stop","id":1}
{"cmd":"servo_mode","id":1,"mode":"motor"}
{"cmd":"servo_motor_set","id":1,"speed":0.25,"accel":40}
{"cmd":"servo_motor_stop","id":1}
{"cmd":"servo_torque","id":1,"enabled":false}
{"cmd":"read","id":1}
{"cmd":"servo_torque_limit","id":1,"limit":250}
{"cmd":"servo_jog","id":1,"delta":2,"base":180,"speed":250,"accel":8,"setup":true}
{"cmd":"servo_torque","id":1,"enabled":true}
{"cmd":"servo_identify","id":1,"amplitude":12}
{"cmd":"home","id":1}
{"cmd":"home","all":true}
{"cmd":"stop"}
{"cmd":"policy_arm","confirm":"CALIBRATED_AND_LIFTED"}
{"cmd":"policy_frame","seq":1,"targets":{"1":180,"2":180,"3":180,"4":180,"5":180,"6":180,"7":180,"8":180,"9":180,"10":180,"11":180,"12":180}}
{"cmd":"policy_disarm"}
{"cmd":"config_get"}
{"cmd":"config_set","servos":[{"id":1,"name":"hip A","min":0,"max":240,"home":120,"invert":false,"enabled":true}]}
{"cmd":"play","loop":false,"steps":[{"ms":400,"poses":{"1":90,"2":150}},{"ms":400,"poses":{"1":120,"2":120}}]}
```

Responses are also JSON lines and include `ok`, `state`, `config`, or `error` message types.

## Hardware Notes

- Power the servos from the board's servo power input. USB-C is for ESP32 data/power only unless your board documentation says otherwise.
- Firmware boot explicitly disables servo torque and never commands Home; use the UI only after feedback and calibration checks.
- Start with the servo horns removed or the robot lifted so a bad range cannot bind the linkage.
- Learned-policy commands remain locked until all 12 servos and the IMU are calibrated. Follow `policy_runtime/README.md`; never bypass its lifted-robot first test.
- Use conservative min/max angles until each joint is mechanically verified.
- To capture limits, support the leg so it cannot fall, use a low torque percentage, jog slowly to each mechanical endpoint, then set Min or Max in the setup flow. The setup panel tracks the commanded setup angle, so endpoint setup does not depend on servo readback working.
- If your servos are ST3215/ST-series serial bus servos, IDs must be unique. The defaults assume IDs 1 and 2.

## Project Layout

```text
firmware/robot-dog-control/  ESP32 PlatformIO firmware
ui/index.html               Standalone browser UI
docs/COMMANDS.md            Protocol details
policy_runtime/             Learned-policy calibration and goal control
```
