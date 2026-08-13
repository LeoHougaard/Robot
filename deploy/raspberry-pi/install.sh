#!/bin/bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: sudo ./deploy/raspberry-pi/install.sh [--user USER] [--serial DEVICE]

Installs the current checkout as the Raspberry Pi policy service. If --serial
is omitted, exactly one device must be present under /dev/serial/by-id.
EOF
}

if [ "${EUID}" -ne 0 ]; then
  echo "Run this installer with sudo." >&2
  exit 1
fi

SERVICE_USER="${SUDO_USER:-}"
SERIAL_DEVICE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --user)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      SERVICE_USER="$2"
      shift 2
      ;;
    --serial)
      [ "$#" -ge 2 ] || { usage >&2; exit 2; }
      SERIAL_DEVICE="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -z "$SERVICE_USER" ] || [ "$SERVICE_USER" = "root" ]; then
  echo "Pass the normal login account with --user (the service will not run as root)." >&2
  exit 1
fi
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "No such service user: $SERVICE_USER" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROBOT_DOG_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
CALIBRATION="$ROBOT_DOG_ROOT/policy_runtime/assembly-1-12dof.calibration.json"

if ! runuser -u "$SERVICE_USER" -- test -r "$ROBOT_DOG_ROOT/policy_runtime/run_policy.py"; then
  echo "$SERVICE_USER cannot read this checkout: $ROBOT_DOG_ROOT" >&2
  exit 1
fi
if ! runuser -u "$SERVICE_USER" -- test -w "$CALIBRATION"; then
  echo "$SERVICE_USER must be able to update the calibration file: $CALIBRATION" >&2
  exit 1
fi

if [ -z "$SERIAL_DEVICE" ]; then
  shopt -s nullglob
  SERIAL_DEVICES=(/dev/serial/by-id/*)
  shopt -u nullglob
  if [ "${#SERIAL_DEVICES[@]}" -ne 1 ]; then
    echo "Expected exactly one /dev/serial/by-id device; found ${#SERIAL_DEVICES[@]}." >&2
    echo "Attach the ESP32 or pass its stable path with --serial." >&2
    exit 1
  fi
  SERIAL_DEVICE="${SERIAL_DEVICES[0]}"
fi
if [ ! -e "$SERIAL_DEVICE" ]; then
  echo "Serial device does not exist: $SERIAL_DEVICE" >&2
  exit 1
fi
case "$SERIAL_DEVICE" in
  /*) ;;
  *)
    echo "Serial device must be an absolute path: $SERIAL_DEVICE" >&2
    exit 1
    ;;
esac

echo "Installing Raspberry Pi packages..."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-numpy python3-serial python3-venv

usermod -aG dialout "$SERVICE_USER"

if [ ! -x "$ROBOT_DOG_ROOT/.venv-pi/bin/python" ]; then
  runuser -u "$SERVICE_USER" -- python3 -m venv --system-site-packages \
    "$ROBOT_DOG_ROOT/.venv-pi"
fi

echo "Validating the policy bundle and Raspberry Pi inference speed..."
runuser -u "$SERVICE_USER" -- env \
  OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  "$ROBOT_DOG_ROOT/.venv-pi/bin/python" \
  "$ROBOT_DOG_ROOT/policy_runtime/benchmark_policy.py" \
  --iterations 250 --require-hz 50
(
  cd "$ROBOT_DOG_ROOT/policy_runtime"
  runuser -u "$SERVICE_USER" -- "$ROBOT_DOG_ROOT/.venv-pi/bin/python" \
    -m unittest -q test_goal_controller.py test_policy_runtime.py
)

escape_environment_value() {
  printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'
}

ROOT_ESCAPED="$(escape_environment_value "$ROBOT_DOG_ROOT")"
SERIAL_ESCAPED="$(escape_environment_value "$SERIAL_DEVICE")"
cat > /etc/default/robot-dog-policy <<EOF
ROBOT_DOG_ROOT="$ROOT_ESCAPED"
ROBOT_DOG_SERIAL="$SERIAL_ESCAPED"
ROBOT_DOG_UI_HOST="0.0.0.0"
ROBOT_DOG_UI_PORT="18765"
OPENBLAS_NUM_THREADS="1"
OMP_NUM_THREADS="1"
MKL_NUM_THREADS="1"
EOF
chmod 0644 /etc/default/robot-dog-policy

install -m 0755 "$SCRIPT_DIR/run-service.sh" /usr/local/bin/robot-dog-policy
sed "s/@SERVICE_USER@/$SERVICE_USER/g" \
  "$SCRIPT_DIR/robot-dog-policy.service.in" \
  > /etc/systemd/system/robot-dog-policy.service
chmod 0644 /etc/systemd/system/robot-dog-policy.service

systemctl daemon-reload
systemctl enable --now robot-dog-policy.service

echo
echo "Robot Dog policy service installed."
echo "Runner URL: http://$(hostname).local:18765"
echo "Pi addresses: $(hostname -I 2>/dev/null || true)"
echo "Status: sudo systemctl status robot-dog-policy"
echo "Logs:   journalctl -u robot-dog-policy -f"
