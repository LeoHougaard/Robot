#!/usr/bin/env bash
set -Eeuo pipefail

readonly PROFILE="${1:-}"
readonly TASK="${2:-}"
readonly EVIDENCE="/workspace/projects/training/diagnostics/control-profile-robot-validation.json"

[[ "$PROFILE" == /workspace/projects/training/control_profiles/*.json && -f "$PROFILE" ]] || {
  printf 'Invalid or missing deployed control profile: %s\n' "$PROFILE" >&2
  exit 2
}
[[ "$TASK" == Isaac-Locomotion-V2-*-Simple-Dog-Direct-v0 ]] || {
  printf 'Invalid V2 validation task: %s\n' "$TASK" >&2
  exit 2
}

mkdir -p "$(dirname "$EVIDENCE")"
rm -f -- "$EVIDENCE"
set +e
PYTHONPATH=/workspace/projects/training \
  /workspace/isaaclab/isaaclab.sh -p \
  /workspace/projects/training/validate_control_profile_robot.py \
  --control_profile "$PROFILE" \
  --task "$TASK" \
  --num_envs 1 \
  --settle_steps 100 \
  --output "$EVIDENCE" \
  --viz=none
validator_exit=$?
set -e
(( validator_exit == 0 )) || {
  printf 'Isaac Lab robot validator exited with status %d.\n' "$validator_exit" >&2
  exit "$validator_exit"
}

/workspace/isaaclab/_isaac_sim/kit/python/bin/python3 - "$EVIDENCE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    evidence = json.load(handle)
if evidence.get("action_count") != 12:
    raise SystemExit(f"Expected final 12-action evidence, received: {evidence}")
standing = evidence.get("standing", {})
if standing.get("terminations") != 0:
    raise SystemExit(f"Standing validation did not pass: {standing}")
print("CONTROL_PROFILE_ROBOT_EVIDENCE_OK=" + json.dumps(evidence, separators=(",", ":")))
PY
