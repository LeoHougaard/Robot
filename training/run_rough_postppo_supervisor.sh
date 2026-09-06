#!/usr/bin/env bash
# Post-PPO evaluation queue for the CurrentBodyV22 Rough125 continuation.
# This file is prepared for review; it does not launch anything by itself.
set -Eeuo pipefail

RUN_ID="20260906T185023Z-train-58941"
WAIT_PID="${1:?exact PPO PID required}"
[[ "$WAIT_PID" =~ ^[0-9]+$ ]] || exit 2
MODE="${2:-}"
[[ -z "$MODE" || "$MODE" == "--resume" ]] || exit 2

TRAINING="/home/leo/isaac-workspace/projects/training"
CTRAINING="/workspace/projects/training"
RUN="$TRAINING/runs/simple_dog/$RUN_ID"
CRUN="$CTRAINING/runs/simple_dog/$RUN_ID"
SOURCE="$TRAINING/reviews/20260906-speed/20260906T170329Z-train-51934-rough-v2/source"
CSOURCE="$CTRAINING/reviews/20260906-speed/20260906T170329Z-train-51934-rough-v2/source"
REVIEW="$TRAINING/reviews/20260906-speed/$RUN_ID-eval-postppo"
CREVIEW="$CTRAINING/reviews/20260906-speed/$RUN_ID-eval-postppo"
BASELINE="$TRAINING/logs/rl_games/quadruped_current_body_v22_assembly_four_leg_linkage_12dof/2026-09-06_17-03-44/nn/last_quadruped_current_body_v22_assembly_four_leg_linkage_12dof_ep_3250_rew_94.82203.pth"
PROFILE="$TRAINING/control_profiles/assembly-four-leg-linkage-12dof-2e249f1a8efc.json"
FIT="$TRAINING/fits/current-v3-8ec33a2eeac8.json"
EVALUATOR_SHA="6209945809bda16a1c4dd896c8327b9a4d0ff1fde616647ac038262d62ff7f3a"

if [[ "$MODE" == "--resume" ]]; then
  test -d "$REVIEW"
else
  test ! -e "$REVIEW"
  mkdir "$REVIEW"
fi
exec >>"$REVIEW/supervisor.log" 2>&1
trap 'printf "failed line %s at %s\n" "$LINENO" "$(date -Is)" > "$REVIEW/status"' ERR

printf 'waiting for PPO %s\n' "$WAIT_PID" > "$REVIEW/status"
echo "supervisor_pid=$$ started=$(date -Is)"
test "$(id -un)" = leo
test -f "$SOURCE/evaluate_delivery_stride.py"
printf '%s  %s\n' "$EVALUATOR_SHA" "$SOURCE/evaluate_delivery_stride.py" | sha256sum -c -
cmp "$RUN/control_profile.json" "$PROFILE"
cmp "$RUN/simulation-fit.json" "$FIT"
test -f "$TRAINING/check_stride_results.py"
printf '%s  %s\n' 63ba44f1c884b6778c4ec1048f471c596ea5b553d342fc2f8c7f0169b1d160fe "$TRAINING/check_stride_results.py" | sha256sum -c -
cp "$TRAINING/check_stride_results.py" "$REVIEW/check_stride_results.py"
printf '%s  %s\n' 744024c2f692f8d4778d81c487e759426fd2820cc2214f067a4146aa51937668 "$BASELINE" | sha256sum -c -

while [[ -r "/proc/$WAIT_PID/cmdline" ]]; do
  cmd="$(tr '\0' ' ' < "/proc/$WAIT_PID/cmdline")"
  [[ "$cmd" == *"$CRUN/source/train_simple_dog.py"* ]] || break
  sleep 10
done
for attempt in {1..12}; do
  [[ "$(cat "$RUN/status")" != running ]] && break
  sleep 5
done
[[ "$(cat "$RUN/status")" == complete ]]
if pgrep -af '[t]rain_simple_dog.py|[e]valuate_delivery_stride.py|[t]rain_delivery_stride.py' > "$REVIEW/conflicting-processes.txt"; then
  echo 'another training/evaluation process is active' >&2
  exit 20
fi

experiment="$(sed -n 's/^Exact experiment name requested from command line: //p' "$RUN/console.log" | tail -1)"
[[ "$experiment" == "$CTRAINING/logs/rl_games/quadruped_current_body_v22_assembly_four_leg_linkage_12dof/2026-09-06_18-50-39" ]]
CANDIDATE_DIR="${experiment/$CTRAINING/$TRAINING}/nn"
mapfile -t candidates < <(find "$CANDIDATE_DIR" -maxdepth 1 -type f -name 'last_*_ep_3750_rew_*.pth' ! -name '*_rew__*' -print)
(( ${#candidates[@]} == 1 ))
CANDIDATE="${candidates[0]}"
printf 'candidate_periodic=%s\n' "$CANDIDATE" > "$REVIEW/checkpoint-selection.txt"
sha256sum "$BASELINE" "$CANDIDATE" "$SOURCE/evaluate_delivery_stride.py" "$RUN/source_manifest.json" "$RUN/control_profile.json" "$RUN/simulation-fit.json" > "$REVIEW/identity.sha256"
printf 'baseline=%s\ncandidate=%s\nsource=%s\n' "$BASELINE" "$CANDIDATE" "$SOURCE" > "$REVIEW/identity.txt"

valid_existing() {
  local label="$1" checkpoint="$2" envs="$3" seconds="$4" expected_sha
  [[ "$MODE" == "--resume" && -f "$REVIEW/$label.json" && -f "$REVIEW/$label-gate.json" ]] || return 1
  expected_sha="$(sha256sum "$checkpoint" | awk '{print $1}')"
  python3 - "$REVIEW/$label.json" "$expected_sha" "$EVALUATOR_SHA" "$envs" "$seconds" <<'PY'
import json, sys
p, checkpoint_sha, source_sha, rows, seconds = sys.argv[1:]
d = json.load(open(p))
assert d.get("completed") is True
assert d.get("checkpoint_sha256") == checkpoint_sha
assert d.get("source_sha256") == source_sha
assert d.get("simulation_seconds") == int(seconds)
assert len(d.get("results", [])) == int(rows)
json.load(open(p.replace(".json", "-gate.json")))
PY
}

run_eval() {
  local label="$1" checkpoint="$2" stage="$3" terrain_kind="$4" envs="$5" seconds="$6" variation="$7"
  shift 7
  local ccheckpoint="${checkpoint/$TRAINING/$CTRAINING}"
  if valid_existing "$label" "$checkpoint" "$envs" "$seconds"; then
    echo "skipping validated existing $label"
    return 0
  fi
  if [[ "$MODE" == "--resume" && -e "$REVIEW/$label.json" ]]; then
    echo "existing $label is invalid and will not be retried" >&2
    return 21
  fi
  local command=(docker exec -w "$CTRAINING" -e "PYTHONPATH=$CSOURCE" -e PYTHONUNBUFFERED=1
    -e OPENBLAS_NUM_THREADS=1 -e OMP_NUM_THREADS=1 -e MKL_NUM_THREADS=1
    -e OMNI_CRASHREPORTER_ENABLED=0 -e SIMPLE_DOG_STARTUP_TIMEOUT_S=120
    -e SIMPLE_DOG_POLICY_FAMILY=current_body_v22
    -e "SIMPLE_DOG_CONTROL_PROFILE=$CTRAINING/control_profiles/assembly-four-leg-linkage-12dof-2e249f1a8efc.json"
    -e "SIMPLE_DOG_SIMULATION_FIT=$CTRAINING/fits/current-v3-8ec33a2eeac8.json"
    isaac-lab-gb10 /workspace/isaaclab/isaaclab.sh -p "$CSOURCE/evaluate_delivery_stride.py"
    --checkpoint "$ccheckpoint" --family v22 --output "$CREVIEW/$label.json"
    --commands --stage "$stage" --terrain-kind "$terrain_kind" --terrain-height-fraction .125
    --num-envs "$envs" --seconds "$seconds" --variation "$variation"
    --headless --device=cuda:0 "$@")
  printf '%q ' "${command[@]}" >> "$REVIEW/commands.sh"
  printf '\n' >> "$REVIEW/commands.sh"
  local attempt=1 log_path="$REVIEW/$label.log" rc=0
  while (( attempt <= 3 )); do
    printf 'evaluating %s attempt %s at %s\n' "$label" "$attempt" "$(date -Is)" > "$REVIEW/status"
    (( attempt > 1 )) && log_path="$REVIEW/$label.attempt${attempt}.log"
    set +e
    "${command[@]}" > "$log_path" 2>&1
    rc=$?
    set -e
    if [[ -f "$REVIEW/$label.json" ]]; then
      python3 - "$REVIEW/$label.json" "$envs" "$seconds" "$EVALUATOR_SHA" "$(sha256sum "$checkpoint" | awk '{print $1}')" <<'PY'
import json,sys
p,rows,seconds,source_sha,checkpoint_sha=sys.argv[1:]
d=json.load(open(p))
assert d.get("completed") is True, d.get("error", "incomplete evaluation")
assert len(d.get("results",[])) == int(rows)
assert d.get("simulation_seconds") == int(seconds)
assert d.get("source_sha256") == source_sha
assert d.get("checkpoint_sha256") == checkpoint_sha
PY
      break
    fi
    if grep -q 'STRIDE_SIM_START' "$log_path" && ! grep -q 'STRIDE_SIM_READY' "$log_path" && (( attempt < 3 )); then
      ((attempt++)); continue
    fi
    (( rc != 0 )) || rc=22
    return "$rc"
  done
  if python3 "$REVIEW/check_stride_results.py" "$REVIEW/$label.json" > "$REVIEW/$label-gate.json"; then
    echo pass > "$REVIEW/$label-gate-status"
  else
    echo fail > "$REVIEW/$label-gate-status"
  fi
}

run_eval baseline-rough-forward20 "$BASELINE" rough uniform 1 20 v20-train-envelope --command-index 8 --video-folder "$CREVIEW/baseline-rough-forward20-video"
run_eval candidate-rough-forward20 "$CANDIDATE" rough uniform 1 20 v20-train-envelope --command-index 8 --video-folder "$CREVIEW/candidate-rough-forward20-video"
for kind in uniform up down; do
  run_eval "baseline-rough-${kind}60" "$BASELINE" rough "$kind" 14 60 v20-train-envelope
  run_eval "candidate-rough-${kind}60" "$CANDIDATE" rough "$kind" 14 60 v20-train-envelope
done
run_eval baseline-speed60 "$BASELINE" speed mixture 14 60 nominal
run_eval candidate-speed60 "$CANDIDATE" speed mixture 14 60 nominal
run_eval baseline-speed-timing60 "$BASELINE" speed mixture 14 60 v20-train-envelope --timing-assessment
run_eval candidate-speed-timing60 "$CANDIDATE" speed mixture 14 60 v20-train-envelope --timing-assessment
printf 'complete at %s\n' "$(date -Is)" > "$REVIEW/status"
