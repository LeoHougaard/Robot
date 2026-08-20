#!/bin/sh
set -eu

: "${ROBOT_DOG_ROOT:?ROBOT_DOG_ROOT is not set}"
: "${ROBOT_DOG_SERIAL:?ROBOT_DOG_SERIAL is not set}"

ROBOT_DOG_UI_HOST="${ROBOT_DOG_UI_HOST:-0.0.0.0}"
ROBOT_DOG_UI_PORT="${ROBOT_DOG_UI_PORT:-18765}"

if [ ! -e "$ROBOT_DOG_SERIAL" ]; then
  echo "Robot Dog ESP32 serial device is not present: $ROBOT_DOG_SERIAL" >&2
  exit 1
fi

exec "$ROBOT_DOG_ROOT/.venv-pi/bin/python" \
  "$ROBOT_DOG_ROOT/policy_runtime/run_policy.py" \
  --ui \
  --ui-host "$ROBOT_DOG_UI_HOST" \
  --ui-port "$ROBOT_DOG_UI_PORT" \
  --port "$ROBOT_DOG_SERIAL"
