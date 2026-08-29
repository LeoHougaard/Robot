# ST3215 protocol and load telemetry

The robot uses the Waveshare ST3215-7.4V serial bus servo at 1 Mbps. Waveshare
publishes the [ST3215 product documentation](https://www.waveshare.com/wiki/ST3215_Servo),
the [ST3215 user manual](https://files.waveshare.com/upload/f/f4/ST3215_Servo_User_Manual.pdf),
and the register spreadsheet copied into this repository as
[`ST3215-memory-register-map-EN.xls`](waveshare_downloads/ST3215-memory-register-map-EN.xls).
The included Waveshare SCServo library defines the same addresses in
[`SMS_STS.h`](waveshare_downloads/scservo/SCServo/SMS_STS.h).

## Packet format

The servo bus uses 8-N-1 TTL UART at 1,000,000 baud. Multi-byte register values
are little-endian.

```text
Request:  FF FF ID LENGTH INSTRUCTION PARAMETERS... CHECKSUM
Response: FF FF ID LENGTH STATUS      DATA...       CHECKSUM
```

`LENGTH` counts the instruction or status byte, parameters or data, and the
checksum. `CHECKSUM` is the low byte of the one's complement of every byte from
`ID` through the last parameter or data byte.

The read instruction is `0x02`. A read of all runtime feedback for servo ID 3
starts at register `0x38` and requests 15 bytes:

```text
FF FF 03 04 02 38 0F AF
```

The firmware's `servo_telemetry` command performs this transaction for one ID
while policy control is idle. During policy control, the firmware instead uses
a synchronized four-byte read at `0x38` for critical position/speed and a
separate synchronized two-byte read at `0x45` for current across all 12 IDs.

## Runtime feedback registers

| Address | Bytes | Field | Interpretation |
|---:|---:|---|---|
| 56 `0x38` | 2 | Present position | 0 to 4095 across 360 degrees |
| 58 `0x3A` | 2 | Present speed | Steps per second, bit 15 is direction |
| 60 `0x3C` | 2 | Present load | Motor-drive voltage duty, bits 0 to 9 are magnitude and bit 10 is direction |
| 62 `0x3E` | 1 | Present voltage | 0.1 V per step |
| 63 `0x3F` | 1 | Present temperature | Degrees Celsius |
| 64 `0x40` | 1 | Asynchronous-write flag | Pending registered-write state |
| 65 `0x41` | 1 | Servo status | Protection and hardware status bits |
| 66 `0x42` | 1 | Moving | 1 while moving, otherwise 0 |
| 69 `0x45` | 2 | Present current | 6.5 mA per step, bit 15 is direction |

Servo status bits 0 through 5 mean voltage, sensor, temperature, current, angle,
and overload respectively. A zero status byte means none of those faults is
reported.

## What "force" means here

The ST3215 has no strain gauge or load cell. Its load register is the voltage
duty applied to the motor, not force in newtons. The current register is more
useful for estimating joint effort, but gearbox friction, acceleration, supply
voltage, servo temperature, and linkage geometry all affect the result.

The app reports the raw signed load and current. It also shows a rough joint
torque estimate for the 7.4 V model:

```text
effective current = max(abs(current) - 0.15 A, 0)
torque             = effective current * 7.8 kg cm/A
```

The 0.15 A no-load current and 7.8 kg cm/A torque constant come from Waveshare's
7.4 V product specification. The estimate is not a calibrated measurement.
Calculating contact force at a foot would also require the active leg geometry,
all relevant joint torques, pose, and a calibration against known loads.

## Mobile timing rule

Firmware 0.1.13 clocks position/speed reads for all 12 servos every 20 ms in the safety-critical
synchronized feedback transaction, then reads current for all 12 in a separate
synchronized transaction. `policy_state` carries aligned angle and current
arrays. Host target writes are independent: `seq` identifies the latest applied
target and `tick` identifies the feedback sample. A missing current reply marks `current_complete:false` but cannot halt
the policy; only three consecutive incomplete critical position/speed frames
disarm it. Compact feedback and the 2 Mbaud Pixel link reduce host-transfer time.
The recorded firmware sample interval is the authority for the achieved rate.
The Pixel converts current to
estimated torque and shows the selected ID, while run recording retains every
available servo current.

When policy control is idle, the Pixel still uses the single-ID
`servo_telemetry` command at 250 ms while the completed stand pose is held. The
ESP32 rejects that separate point read during policy control because the
synchronized policy read already contains all servo data.
