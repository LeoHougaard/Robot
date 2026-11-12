#!/usr/bin/env bash
# Review-only post-queue for the isolated CurrentBodyV22 RoughBumps25 stage.
# It waits for the existing Rough125 post-PPO supervisor, then evaluates the
# fixed epoch-3250 baseline under the larger-bump profile.
set -Eeuo pipefail

WAIT_SUPERVISOR="${1:-2062239}"
TRAINING="/home/leo/isaac-workspace/projects/training"
CTRAINING="/workspace/projects/training"
REVIEW="$TRAINING/reviews/bumps25-20260906"
CREVIEW="$CTRAINING/reviews/bumps25-20260906"
SOURCE="$REVIEW/source"
CSOURCE="$CREVIEW/source"
BASELINE="$TRAINING/logs/rl_games/quadruped_current_body_v22_assembly_four_leg_linkage_12dof/2026-09-06_17-03-44/nn/last_quadruped_current_body_v22_assembly_four_leg_linkage_12dof_ep_3250_rew_94.82203.pth"
PROFILE="$TRAINING/control_profiles/assembly-four-leg-linkage-12dof-2e249f1a8efc.json"
FIT="$TRAINING/fits/current-v3-8ec33a2eeac8.json"
EVALUATOR_SHA="c3453cca364202e869413a6232a4de03a336ed4a195c5dc215de80e4752cd9e6"
GATE_SHA="63ba44f1c884b6778c4ec1048f471c596ea5b553d342fc2f8c7f0169b1d160fe"

[[ "$WAIT_SUPERVISOR" =~ ^[0-9]+$ ]]
[[ -d "$SOURCE" && -f "$REVIEW/manifest.json" ]]
[[ "$(id -un)" == leo ]]
[[ -f "$BASELINE" && -f "$PROFILE" && -f "$FIT" ]]
printf '%s\n' "744024c2f692f8d4778d81c487e759426fd2820cc2214f067a4146aa51937668  $BASELINE" | sha256sum -c -
printf '%s\n' "$EVALUATOR_SHA  $SOURCE/evaluate_delivery_stride.py" | sha256sum -c -
python3 - "$REVIEW/manifest.json" "$SOURCE" <<'PY'
import hashlib,json,sys
from pathlib import Path,PurePosixPath
manifest=json.load(open(sys.argv[1]))
root=Path(sys.argv[2]).resolve()
for name,digest in manifest['source_files'].items():
    relative=PurePosixPath(name.removeprefix('training/'))
    path=(root/str(relative)).resolve()
    assert path.is_relative_to(root)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==digest, name
PY
cmp "$TRAINING/runs/simple_dog/20260906T185023Z-train-58941/control_profile.json" "$PROFILE"
cmp "$TRAINING/runs/simple_dog/20260906T185023Z-train-58941/simulation-fit.json" "$FIT"
printf '%s\n' "$GATE_SHA  $TRAINING/check_stride_results.py" | sha256sum -c -
cp "$TRAINING/check_stride_results.py" "$REVIEW/check_stride_results.py"
docker exec isaac-lab-gb10 test -f "$CSOURCE/evaluate_delivery_stride.py"
docker exec isaac-lab-gb10 test -f "$CTRAINING/control_profiles/assembly-four-leg-linkage-12dof-2e249f1a8efc.json"
docker exec isaac-lab-gb10 test -f "$CTRAINING/fits/current-v3-8ec33a2eeac8.json"
printf '%s\n' "$(sha256sum "$BASELINE")" > "$REVIEW/baseline.sha256"
exec >>"$REVIEW/supervisor.log" 2>&1
trap 'printf "failed line %s at %s\n" "$LINENO" "$(date -Is)" > "$REVIEW/status"' ERR
printf 'waiting for rough evaluation supervisor %s at %s\n' "$WAIT_SUPERVISOR" "$(date -Is)" > "$REVIEW/status"

while [[ -r "/proc/$WAIT_SUPERVISOR/cmdline" ]]; do
  cmd="$(tr '\0' ' ' < "/proc/$WAIT_SUPERVISOR/cmdline")"
  [[ "$cmd" == *"run_rough_postppo_supervisor.sh 2035421"* ]] || {
    printf 'unexpected supervisor identity: %s\n' "$cmd" >&2
    exit 20
  }
  sleep 30
done
if pgrep -af '[t]rain_simple_dog.py|[e]valuate_delivery_stride.py|[t]rain_delivery_stride.py' > "$REVIEW/conflicting-processes.txt"; then
  printf 'another training/evaluation process is active\n' >&2
  exit 21
fi
[[ -z "$(nvidia-smi --query-compute-apps=pid --format=csv,noheader)" ]]

printf 'starting bumps25 baseline queue at %s\n' "$(date -Is)" > "$REVIEW/status"

run_eval() {
  local label="$1" envs="$2" seconds="$3" video="$4"
  local output="$REVIEW/$label.json" coutput="$CREVIEW/$label.json" log="$REVIEW/$label.log"
  [[ ! -e "$output" && ! -e "$REVIEW/$label-video" ]]
  local command=(docker exec -w "$CTRAINING" -e "PYTHONPATH=$CSOURCE" -e PYTHONUNBUFFERED=1
    -e OPENBLAS_NUM_THREADS=1 -e OMP_NUM_THREADS=1 -e MKL_NUM_THREADS=1
    -e OMNI_CRASHREPORTER_ENABLED=0 -e SIMPLE_DOG_STARTUP_TIMEOUT_S=120
    -e SIMPLE_DOG_POLICY_FAMILY=current_body_v22
    -e "SIMPLE_DOG_CONTROL_PROFILE=$CTRAINING/control_profiles/assembly-four-leg-linkage-12dof-2e249f1a8efc.json"
    -e "SIMPLE_DOG_SIMULATION_FIT=$CTRAINING/fits/current-v3-8ec33a2eeac8.json"
    isaac-lab-gb10 /workspace/isaaclab/isaaclab.sh -p "$CSOURCE/evaluate_delivery_stride.py"
    --checkpoint "$CTRAINING/logs/rl_games/quadruped_current_body_v22_assembly_four_leg_linkage_12dof/2026-09-06_17-03-44/nn/last_quadruped_current_body_v22_assembly_four_leg_linkage_12dof_ep_3250_rew_94.82203.pth"
    --family v22 --output "$coutput" --commands --stage rough --terrain-profile bumps25
    --terrain-kind uniform --terrain-height-fraction .125 --num-envs "$envs" --seconds "$seconds"
    --variation v20-train-envelope --headless --device=cuda:0)
  if [[ "$video" == 1 ]]; then
    command+=(--command-index 8 --video-folder "$CREVIEW/$label-video")
  fi
  printf '%q ' "${command[@]}" >> "$REVIEW/commands.sh"; printf '\n' >> "$REVIEW/commands.sh"
  for attempt in 1 2 3; do
    printf 'evaluating %s attempt %s at %s\n' "$label" "$attempt" "$(date -Is)" > "$REVIEW/status"
    "${command[@]}" >"$log" 2>&1 || true
    [[ -f "$output" ]] || {
      if (( attempt < 3 )) && grep -q 'STRIDE_SIM_START' "$log" && ! grep -q 'STRIDE_SIM_READY' "$log"; then
        log="$REVIEW/$label.attempt$((attempt + 1)).log"
        continue
      fi
      return 22
    }
    python3 - "$output" "$envs" "$seconds" "$EVALUATOR_SHA" "$BASELINE" <<'PY'
import hashlib, json, sys
p, rows, seconds, source_sha, checkpoint = sys.argv[1:]
d=json.load(open(p))
assert d.get('completed') is True
assert len(d.get('results', [])) == int(rows)
assert d.get('simulation_seconds') == int(seconds)
assert d.get('source_sha256') == source_sha
assert d.get('checkpoint_sha256') == hashlib.sha256(open(checkpoint,'rb').read()).hexdigest()
assert d.get('terrain_profile') == 'bumps25'
assert d.get('bump_height_range_m') == [.0025, .006]
PY
    if python3 "$REVIEW/check_stride_results.py" "$output" >"${output%.json}-gate.json"; then
      printf 'pass\n' > "$REVIEW/$label-gate-status"
    else
      printf 'fail\n' > "$REVIEW/$label-gate-status"
    fi
    python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert isinstance(d,dict) and all(isinstance(v,list) for v in d.values())' "${output%.json}-gate.json"
    return 0
  done
}

run_eval baseline-bumps25-uniform20 14 20 0
run_eval baseline-bumps25-uniform20-video 1 20 1
printf 'complete at %s\n' "$(date -Is)" > "$REVIEW/status"
