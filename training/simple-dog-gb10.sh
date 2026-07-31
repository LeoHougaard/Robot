#!/usr/bin/env bash
set -Eeuo pipefail

readonly CONTAINER="isaac-lab-gb10"
readonly ROOT="/home/leo/isaac-workspace/projects/training"
readonly RUNS_ROOT="${ROOT}/runs/simple_dog"

active() {
  docker top "$CONTAINER" -eo pid,args 2>/dev/null |
    grep -F '[t]rain_simple_dog.py' >/dev/null
}

latest_run() {
  find "$RUNS_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null |
    sort |
    tail -1
}

start_training() {
  local num_envs="${1:-512}"
  local max_iterations="${2:-500}"
  local checkpoint="${3:-}"
  local tuning_config="${4:-}"
  local terrain="${5:-flat}"
  local before latest

  [[ "$num_envs" =~ ^[0-9]+$ ]] && ((num_envs >= 128 && num_envs <= 16384)) ||
    { printf 'Invalid environment count: %s\n' "$num_envs" >&2; exit 2; }
  [[ "$max_iterations" =~ ^[0-9]+$ ]] && ((max_iterations >= 1 && max_iterations <= 100000)) ||
    { printf 'Invalid iteration count: %s\n' "$max_iterations" >&2; exit 2; }
  [[ "$terrain" == flat || "$terrain" == rough || "$terrain" == rough_noscan ||
     "$terrain" == v2core || "$terrain" == v2robust ||
     "$terrain" == v2goal ]] ||
    { printf 'Invalid terrain: %s\n' "$terrain" >&2; exit 2; }
  [[ "$terrain" != v2robust && "$terrain" != v2goal ]] ||
    [[ -n "$checkpoint" ]] ||
    { printf '%s requires a passing V2 checkpoint.\n' "$terrain" >&2; exit 2; }
  [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" == true ]] ||
    { printf '%s is not running.\n' "$CONTAINER" >&2; exit 1; }
  [[ -x "${ROOT}/run_simple_dog.sh" ]] ||
    { printf 'Training launcher is missing: %s\n' "${ROOT}/run_simple_dog.sh" >&2; exit 1; }
  if [[ -n "$checkpoint" ]]; then
    [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_rough_velocity_direct/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ]] ||
      { printf 'Checkpoint is outside the simple-dog log directory: %s\n' "$checkpoint" >&2; exit 2; }
    if [[ "$terrain" == v2* ]]; then
      [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ]] ||
        { printf 'V2 terrain requires a V2 checkpoint because policy observations differ.\n' >&2; exit 2; }
    else
      [[ "$checkpoint" != /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ]] ||
        { printf 'A V2 checkpoint cannot be loaded into a V1 task.\n' >&2; exit 2; }
    fi
    docker exec "$CONTAINER" test -f "$checkpoint" ||
      { printf 'Checkpoint does not exist: %s\n' "$checkpoint" >&2; exit 2; }
  fi
  if [[ -n "$tuning_config" ]]; then
    [[ "$tuning_config" == /workspace/projects/autoresearch/*.json ]] ||
      { printf 'Tuning config is outside /workspace/projects/autoresearch: %s\n' "$tuning_config" >&2; exit 2; }
    docker exec "$CONTAINER" test -f "$tuning_config" ||
      { printf 'Tuning config does not exist: %s\n' "$tuning_config" >&2; exit 2; }
  fi

  if active; then
    printf 'Simple-dog training is already active.\n' >&2
    status_training
    return 3
  fi

  before="$(latest_run || true)"
  docker exec -d \
    --workdir /workspace/projects/training \
    -e "SIMPLE_DOG_NUM_ENVS=${num_envs}" \
    -e "SIMPLE_DOG_MAX_ITERATIONS=${max_iterations}" \
    -e "SIMPLE_DOG_CHECKPOINT=${checkpoint}" \
    -e "SIMPLE_DOG_TUNING_CONFIG=${tuning_config}" \
    -e "SIMPLE_DOG_TERRAIN=${terrain}" \
    "$CONTAINER" \
    /bin/bash /workspace/projects/training/run_simple_dog.sh train

  for _ in $(seq 1 20); do
    latest="$(latest_run || true)"
    if [[ -n "$latest" && "$latest" != "$before" ]]; then
      printf '%s\n' "$latest"
      return 0
    fi
    sleep 1
  done
  printf 'Detached process did not create a run directory.\n' >&2
  return 1
}

status_training() {
  local container latest status progress reward gpu experiment

  container="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || printf 'absent')"
  printf 'Container: %s\n' "$container"
  latest="$(latest_run || true)"
  if [[ -z "$latest" ]]; then
    printf 'Training:  no runs found\n'
    return 0
  fi

  status="$(cat "$latest/status" 2>/dev/null || printf 'unknown')"
  if [[ "$status" == running && "$container" != running ]]; then
    status="interrupted (container is not running; launcher state was stale)"
  fi
  printf 'Training:  %s\n' "$status"
  printf 'Run data:  %s\n' "$latest"
  [[ ! -f "$latest/num_envs" ]] || { printf 'Envs:      '; cat "$latest/num_envs"; }
  [[ ! -f "$latest/max_iterations" ]] || { printf 'Target:    '; cat "$latest/max_iterations"; }

  progress="$(grep -E 'fps step: .*epoch:' "$latest/console.log" 2>/dev/null | tail -1 || true)"
  [[ -z "$progress" ]] || printf 'Progress:  %s\n' "$progress"
  reward="$(grep -E 'saving next best rewards:' "$latest/console.log" 2>/dev/null | tail -1 || true)"
  [[ -z "$reward" ]] || printf 'Best:      %s\n' "$reward"

  if [[ "$container" == running ]]; then
    gpu="$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader 2>/dev/null || true)"
    [[ -z "$gpu" ]] || printf 'GPU:       %s\n' "$gpu"
  fi

  experiment="$(
    grep -E 'Exact experiment name requested from command line: /workspace/projects/' \
      "$latest/console.log" 2>/dev/null |
      tail -1 |
      sed 's/.*: //' || true
  )"
  [[ -z "$experiment" ]] || printf 'Outputs:   %s\n' "${experiment#/workspace/projects/}"
}

case "${1:-}" in
  active)
    active
    ;;
  start)
    start_training "${2:-512}" "${3:-500}" "${4:-}" "${5:-}" "${6:-flat}"
    ;;
  status)
    status_training
    ;;
  *)
    printf 'Usage: %s active|start [num-envs max-iterations checkpoint tuning-config terrain]|status\n' "$0" >&2
    exit 2
    ;;
esac
