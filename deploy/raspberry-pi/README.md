# Raspberry Pi 3B policy host

A Raspberry Pi 3B can replace the Windows policy runner. The Pi runs the same
NumPy actor and guarded 50 Hz USB transport from `policy_runtime/run_policy.py`;
the ESP32 still owns servo I/O and its stale-command watchdog. No training code
or Isaac Lab installation is needed on the Pi.

Use **64-bit Raspberry Pi OS Bookworm Lite**. Connect the ESP32 to a Pi USB port
and put the Pi and the browser device on the same trusted Wi-Fi network. Do not
forward TCP port 18765 through a router: its API can command physical motion and
is intentionally intended only for the robot's local network.

Power the Pi from a stable 5 V supply and keep the 7.4 V servo rail on its own
proper supply; do not power the servos from the Pi USB or GPIO rail. Ethernet is
the most reliable remote-control link, but the Pi 3B's Wi-Fi is usable when the
signal is strong. A network interruption longer than the UI heartbeat window
will disarm control.

## Install

Clone or copy this repository to the Pi as the normal login user. Do not use an
old OneDrive copy. From the repository root, run:

```bash
chmod +x deploy/raspberry-pi/install.sh
sudo ./deploy/raspberry-pi/install.sh
```

The installer:

- installs the Raspberry Pi OS NumPy and pyserial packages without compiling
  them on the Pi;
- creates `.venv-pi` with access to those optimized system packages;
- verifies the policy hash/metadata, benchmarks actor inference, and runs all
  policy-runtime unit tests;
- grants the login user serial access through `dialout`;
- records the ESP32's stable `/dev/serial/by-id/...` device;
- installs and starts `robot-dog-policy.service` at boot;
- exposes the policy bridge on the Pi's trusted-LAN interfaces at port 18765.

If more than one USB serial adapter is connected, choose the ESP32 explicitly:

```bash
ls -l /dev/serial/by-id/
sudo ./deploy/raspberry-pi/install.sh \
  --serial /dev/serial/by-id/usb-YOUR_ESP32_ADAPTER
```

When logged in directly as root, also pass the intended non-root service user,
for example `--user leo`. The repository and calibration file must be writable
by that account because center and IMU alignment changes are saved there.

## Connect the browser UI

Open the ESP32-hosted UI as before. In **Remote Control**, change **Runner URL**
to:

```text
http://raspberrypi.local:18765
```

Replace `raspberrypi` with the Pi hostname selected during Raspberry Pi OS
setup. If `.local` names do not resolve on the browser device, use an address
printed by the installer, for example `http://192.168.1.42:18765`. The UI saves
this choice in browser storage. The browser, Pi, and ESP32 UI must use plain
HTTP on the local network; an HTTPS page cannot call the Pi's plain-HTTP bridge.

The Pi remains connected after the Windows computer is shut down. The browser
is still the remote control: its command heartbeat must remain present while
the policy is active, and losing it requests Stop + Disarm.

## Operate and diagnose

```bash
sudo systemctl status robot-dog-policy
journalctl -u robot-dog-policy -f
curl http://127.0.0.1:18765/api/status
```

Stop + Disarm before maintenance, then stop the service:

```bash
curl -X POST -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:18765/api/stop
sudo systemctl stop robot-dog-policy
```

`systemctl stop` sends SIGINT so the runner also executes its Stop + Disarm
cleanup. Disconnecting USB during motion still triggers the independent ESP32
120 ms stale-command watchdog.

After changing the checkout, rerun the installer to repeat validation and
restart the service. To select a replacement USB adapter, rerun it with the new
stable `--serial` path. Service configuration is stored in
`/etc/default/robot-dog-policy`.

If the Remote Control buttons are unavailable, use the status and log commands
above. Typical causes are:

- **runner offline**: wrong Pi hostname/IP, the service is stopped, or client
  isolation prevents Wi-Fi devices from reaching one another;
- **serial device is not present**: USB cable/power changed or the saved
  `/dev/serial/by-id` adapter is no longer attached;
- **permission denied**: the service user is not in `dialout`;
- **policy/calibration rejected**: the copied bundle or calibration differs
  from the validated repository state.

The installation benchmark measures actor inference only. Before relying on
the Pi for motion, securely support the robot, start feedback, and confirm the
live policy loop reports approximately 50 Hz with no feedback/sequence errors.
