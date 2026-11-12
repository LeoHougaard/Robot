#!/usr/bin/env bash
# Bounded evaluation of the September 6 speed experiment; never deploys a policy.
set -Eeuo pipefail
RUN_ID="20260906T170329Z-train-51934"
WAIT_PID="${1:?exact PPO PID required}"
[[ "$WAIT_PID" =~ ^[0-9]+$ ]] || exit 2
MODE="${2:-}"
[[ -z "$MODE" || "$MODE" == "--resume" ]] || exit 2
TRAINING="/home/leo/isaac-workspace/projects/training"
CTRAINING="/workspace/projects/training"
RUN="$TRAINING/runs/simple_dog/$RUN_ID"
CRUN="$CTRAINING/runs/simple_dog/$RUN_ID"
SOURCE="$RUN/source"
CSOURCE="$CRUN/source"
REVIEW="$TRAINING/reviews/20260906-speed/$RUN_ID-eval-v3"
CREVIEW="$CTRAINING/reviews/20260906-speed/$RUN_ID-eval-v3"
BASELINE="$TRAINING/logs/rl_games/quadruped_current_body_v22_assembly_four_leg_linkage_12dof/2026-09-06_10-33-19/nn/last_quadruped_current_body_v22_assembly_four_leg_linkage_12dof_ep_2750_rew_336.68628.pth"
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
cmp "$RUN/control_profile.json" "$TRAINING/control_profiles/assembly-four-leg-linkage-12dof-2e249f1a8efc.json"
cmp "$RUN/simulation-fit.json" "$TRAINING/fits/current-v3-8ec33a2eeac8.json"
cp "$TRAINING/check_stride_results.py" "$REVIEW/check_stride_results.py"
printf '%s  %s\n' ed52b4f3502fb3d0fb64ad4b258c3cdc623e37d0fab96dad02e8f11a1644e940 "$BASELINE" | sha256sum -c -
while [[ -r "/proc/$WAIT_PID/cmdline" ]]; do
  cmd="$(tr '\0' ' ' < "/proc/$WAIT_PID/cmdline")"
  [[ "$cmd" == *"$CSOURCE/train_simple_dog.py"* ]] || break
  sleep 10
done
# The launcher writes status shortly after its Python child returns.
for attempt in {1..12}; do
  [[ "$(cat "$RUN/status")" != running ]] && break
  sleep 5
done
[[ "$(cat "$RUN/status")" == complete ]]
if pgrep -af '[t]rain_simple_dog.py|[e]valuate_delivery_stride.py|[t]rain_delivery_stride.py' > "$REVIEW/conflicting-processes.txt"; then
  echo 'another training/evaluation process is active' >&2
  exit 20
fi
CANDIDATE_DIR="$TRAINING/logs/rl_games/quadruped_current_body_v22_assembly_four_leg_linkage_12dof/2026-09-06_17-03-44/nn"
# Periodic and final RL-Games saves have different archive names but identical
# actor tensors and epoch/frame. Pin the verified periodic checkpoint bytes.
CANDIDATE="$CANDIDATE_DIR/last_quadruped_current_body_v22_assembly_four_leg_linkage_12dof_ep_3250_rew_94.82203.pth"
printf '%s  %s\n' 744024c2f692f8d4778d81c487e759426fd2820cc2214f067a4146aa51937668 "$CANDIDATE" | sha256sum -c -
sha256sum "$BASELINE" "$CANDIDATE" "$SOURCE/evaluate_delivery_stride.py" "$RUN/source_manifest.json" "$RUN/control_profile.json" "$RUN/simulation-fit.json" "$REVIEW/check_stride_results.py" > "$REVIEW/identity.sha256"
printf 'baseline=%s\ncandidate=%s\nsource=%s\n' "$BASELINE" "$CANDIDATE" "$SOURCE" > "$REVIEW/identity.txt"
EVALUATOR_SHA="$(sha256sum "$SOURCE/evaluate_delivery_stride.py" | awk '{print $1}')"
valid_existing() {
  local label="$1" checkpoint="$2" envs="$3" seconds="$4" expected_sha
  [[ "$MODE" == "--resume" && -f "$REVIEW/$label.json" && -f "$REVIEW/$label-gate.json" ]] || return 1
  expected_sha="$(sha256sum "$checkpoint" | awk '{print $1}')"
  python3 - "$REVIEW/$label.json" "$expected_sha" "$EVALUATOR_SHA" "$envs" "$seconds" <<'PY'
import json,sys
p,checkpoint_sha,source_sha,rows,seconds=sys.argv[1:]
d=json.load(open(p))
assert d.get("completed") is True
assert d.get("checkpoint_sha256") == checkpoint_sha
assert d.get("source_sha256") == source_sha
assert d.get("simulation_seconds") == int(seconds)
assert len(d.get("results", [])) == int(rows)
json.load(open(p.replace('.json','-gate.json')))
PY
}
run_eval() {
  local label="$1" checkpoint="$2" stage="$3" envs="$4" seconds="$5"
  shift 5
  local ccheckpoint="${checkpoint/$TRAINING/$CTRAINING}"
  if valid_existing "$label" "$checkpoint" "$envs" "$seconds"; then
    echo "skipping validated existing $label"
    return 0
  fi
  if [[ "$MODE" == "--resume" && -e "$REVIEW/$label.json" ]]; then
    echo "existing $label is invalid and will not be retried" >&2
    return 21
  fi
  printf 'evaluating %s at %s\n' "$label" "$(date -Is)" > "$REVIEW/status"
  local command=(docker exec -w "$CTRAINING" -e "PYTHONPATH=$CSOURCE" -e PYTHONUNBUFFERED=1
    -e OPENBLAS_NUM_THREADS=1 -e OMP_NUM_THREADS=1 -e MKL_NUM_THREADS=1
    -e OMNI_CRASHREPORTER_ENABLED=0 -e SIMPLE_DOG_STARTUP_TIMEOUT_S=120
    -e SIMPLE_DOG_POLICY_FAMILY=current_body_v22
    -e "SIMPLE_DOG_CONTROL_PROFILE=$CTRAINING/control_profiles/assembly-four-leg-linkage-12dof-2e249f1a8efc.json"
    -e "SIMPLE_DOG_SIMULATION_FIT=$CTRAINING/fits/current-v3-8ec33a2eeac8.json"
    isaac-lab-gb10 /workspace/isaaclab/isaaclab.sh -p "$CSOURCE/evaluate_delivery_stride.py"
    --checkpoint "$ccheckpoint" --family v22 --output "$CREVIEW/$label.json"
    --commands --stage "$stage" --num-envs "$envs" --seconds "$seconds"
    --headless --device=cuda:0 "$@")
  printf '%q ' "${command[@]}" >> "$REVIEW/commands.sh"
  printf '\n' >> "$REVIEW/commands.sh"
  local attempt=1 log_path="$REVIEW/$label.log" rc=0
  while (( attempt <= 3 )); do
    printf 'evaluating %s attempt %s at %s\n' "$label" "$attempt" "$(date -Is)" > "$REVIEW/status"
    if (( attempt == 1 )) && [[ "$MODE" == "--resume" && -f "$log_path" ]]; then
      :
    else
      (( attempt > 1 )) && log_path="$REVIEW/$label.attempt${attempt}.log"
      set +e
      "${command[@]}" > "$log_path" 2>&1
      rc=$?
      set -e
    fi
    if [[ -f "$REVIEW/$label.json" ]]; then
      python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d.get("completed") is True and len(d.get("results",[])) == int(sys.argv[2]), d.get("error", "incomplete evaluation")' "$REVIEW/$label.json" "$envs"
      break
    fi
    if grep -q 'STRIDE_SIM_START' "$log_path" && ! grep -q 'STRIDE_SIM_READY' "$log_path" && (( attempt < 3 )); then
      ((attempt++)); continue
    fi
    # A wrapper can return zero without producing an evaluation result.
    # Missing results must still stop the queue as an infrastructure error.
    (( rc != 0 )) || rc=22
    return "$rc"
  done
  # A failed behavior gate is evidence, not an infrastructure failure.
  if python3 "$REVIEW/check_stride_results.py" "$REVIEW/$label.json" > "$REVIEW/$label-gate.json"; then
    echo pass > "$REVIEW/$label-gate-status"
  else
    echo fail > "$REVIEW/$label-gate-status"
  fi
}
run_eval baseline-slow20 "$BASELINE" commands 8 20
run_eval candidate-slow20 "$CANDIDATE" commands 8 20
run_eval baseline-speed20 "$BASELINE" speed 14 20
run_eval candidate-speed20 "$CANDIDATE" speed 14 20
if [[ "$(cat "$REVIEW/candidate-slow20-gate-status")" == pass && "$(cat "$REVIEW/candidate-speed20-gate-status")" == pass ]]; then
  run_eval baseline-slow60 "$BASELINE" commands 8 60
  run_eval candidate-slow60 "$CANDIDATE" commands 8 60
  run_eval baseline-speed60 "$BASELINE" speed 14 60
  run_eval candidate-speed60 "$CANDIDATE" speed 14 60
  run_eval candidate-speed-variation60 "$CANDIDATE" speed 14 60 --variation v20-train-envelope
  run_eval candidate-speed-timing60 "$CANDIDATE" speed 14 60 --variation v20-train-envelope --timing-assessment
  run_eval candidate-speed-stationary60 "$CANDIDATE" speed 14 60 --start-stationary
fi
run_eval candidate-fast-forward60 "$CANDIDATE" speed 1 60 --command-index 8 --video-folder "$CREVIEW/candidate-fast-forward60-video"
run_eval candidate-fast-turn60 "$CANDIDATE" speed 1 60 --command-index 12 --video-folder "$CREVIEW/candidate-fast-turn60-video"
printf 'complete at %s\n' "$(date -Is)" > "$REVIEW/status"
