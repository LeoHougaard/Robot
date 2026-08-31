#!/usr/bin/env bash
set -Eeuo pipefail

readonly ROOT="/home/leo/isaac-workspace/projects/training"
readonly RUNS_ROOT="${ROOT}/runs/simple_dog"
readonly HELPER="${ROOT}/simple-dog-gb10.sh"
readonly CONTAINER="isaac-lab-gb10"

initial_run_id="${1:-}"
profile_id="${2:-}"
profile_sha="${3:-}"
simulation_fit_sha="${4:-}"
shift 4 || true
seeds=("$@")

[[ "$initial_run_id" =~ ^[0-9]{8}T[0-9]{6}Z-train-[0-9]+$ ]] || {
  printf 'Initial run id is invalid.\n' >&2
  exit 2
}
[[ "$profile_id" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]] || {
  printf 'Profile id is invalid.\n' >&2
  exit 2
}
[[ "$profile_sha" =~ ^[a-f0-9]{64}$ ]] || {
  printf 'Profile SHA is invalid.\n' >&2
  exit 2
}
[[ "$simulation_fit_sha" =~ ^[a-f0-9]{64}$ ]] || {
  printf 'Simulation-fit SHA is invalid.\n' >&2
  exit 2
}
(( ${#seeds[@]} > 0 )) || {
  printf 'At least one queued seed is required.\n' >&2
  exit 2
}
for seed in "${seeds[@]}"; do
  [[ "$seed" =~ ^[0-9]+$ ]] || {
    printf 'Queued seed is invalid: %s\n' "$seed" >&2
    exit 2
  }
done

readonly CONTROL_PROFILE="/workspace/projects/training/control_profiles/${profile_id}-${profile_sha:0:12}.json"
readonly SIMULATION_FIT="/workspace/projects/training/fits/current-body-v4-${simulation_fit_sha:0:12}.json"
readonly QUEUE_ROOT="${ROOT}/runs/simple_dog_seed_queues"
queue_id="$(date -u +%Y%m%dT%H%M%SZ)-scratch-${initial_run_id##*-}"
queue_dir="${QUEUE_ROOT}/${queue_id}"
mkdir -p "$queue_dir"
printf 'running\n' >"${queue_dir}/status"
printf '%s\n' "$initial_run_id" >"${queue_dir}/initial_run_id"
printf '%s\n' "${seeds[@]}" >"${queue_dir}/queued_seeds"

queue_complete=0
on_exit() {
  exit_code=$?
  printf '%s\n' "$exit_code" >"${queue_dir}/exit_code"
  if (( queue_complete )); then
    printf 'complete\n' >"${queue_dir}/status"
  else
    printf 'failed\n' >"${queue_dir}/status"
  fi
}
trap on_exit EXIT

latest_run_id() {
  find "$RUNS_ROOT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null |
    sort |
    tail -1
}

wait_for_exact_run() {
  local run_id="$1"
  local run_dir="${RUNS_ROOT}/${run_id}"
  printf '%s\n' "$run_id" >"${queue_dir}/current_run_id"
  while "$HELPER" active; do
    [[ "$(latest_run_id)" == "$run_id" ]] || {
      printf 'Active run changed while waiting for %s.\n' "$run_id" >&2
      return 1
    }
    sleep 30
  done
  [[ "$(latest_run_id)" == "$run_id" ]] || {
    printf 'Latest run changed before %s completed.\n' "$run_id" >&2
    return 1
  }
  [[ "$(cat "${run_dir}/status" 2>/dev/null || true)" == complete ]] || {
    printf 'Run did not complete successfully: %s\n' "$run_id" >&2
    return 1
  }
  printf '%s\n' "$run_id" >>"${queue_dir}/completed_run_ids"
}

restart_idle_container() {
  if "$HELPER" active; then
    printf 'Refusing to restart %s while training is active.\n' "$CONTAINER" >&2
    return 1
  fi
  # A completed Isaac process can leave Kit runtime state that survives inside
  # the reusable container even after Python exits.  A narrow container
  # restart clears that state while preserving the mounted project, completed
  # logs, and checkpoints before the next independent seed starts.
  docker stop "$CONTAINER" >/dev/null
  docker start "$CONTAINER" >/dev/null
  for _ in $(seq 1 30); do
    if [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" == true ]]; then
      return 0
    fi
    sleep 1
  done
  printf '%s did not become ready after its clean restart.\n' "$CONTAINER" >&2
  return 1
}

wait_for_exact_run "$initial_run_id"

initial_run_dir="${RUNS_ROOT}/${initial_run_id}"
num_envs="$(cat "${initial_run_dir}/num_envs")"
max_iterations="$(cat "${initial_run_dir}/max_iterations")"
[[ "$num_envs" =~ ^[0-9]+$ ]] || {
  printf 'Initial run environment count is invalid.\n' >&2
  exit 2
}
[[ "$max_iterations" =~ ^[0-9]+$ ]] || {
  printf 'Initial run iteration count is invalid.\n' >&2
  exit 2
}

for seed in "${seeds[@]}"; do
  printf '%s\n' "$seed" >"${queue_dir}/launching_seed"
  restart_idle_container
  run_dir="$($HELPER start \
    "$num_envs" "$max_iterations" '' '' currentbodyv5hard \
    "$CONTROL_PROFILE" "$profile_sha" \
    0 4000 600 "$SIMULATION_FIT" "$seed")"
  run_id="$(basename "$run_dir")"
  [[ "$run_id" =~ ^[0-9]{8}T[0-9]{6}Z-train-[0-9]+$ ]] || {
    printf 'Queued seed %s did not return a valid run id.\n' "$seed" >&2
    exit 1
  }
  printf '%s,%s\n' "$seed" "$run_id" >>"${queue_dir}/launched_runs.csv"
  wait_for_exact_run "$run_id"
done

queue_complete=1
printf 'Scratch seed queue complete: %s\n' "$queue_id"
