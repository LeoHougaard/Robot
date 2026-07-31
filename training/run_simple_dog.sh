#!/usr/bin/env bash
set -Eeuo pipefail

readonly TRAINING_ROOT="/workspace/projects/training"
readonly RUNS_ROOT="${TRAINING_ROOT}/runs/simple_dog"

terrain="${SIMPLE_DOG_TERRAIN:-flat}"
if [[ "$terrain" == v2robust || "$terrain" == v2goal ]] &&
   [[ -z "${SIMPLE_DOG_CHECKPOINT:-}" ]]; then
  printf '%s requires a passing V2 checkpoint.\n' "$terrain" >&2
  exit 2
fi
case "$terrain" in
  flat)
    readonly TASK_NAME="Isaac-Velocity-Flat-Simple-Dog-Direct-v0"
    ;;
  rough)
    readonly TASK_NAME="Isaac-Velocity-Rough-Simple-Dog-Direct-v0"
    ;;
  rough_noscan)
    readonly TASK_NAME="Isaac-Velocity-Rough-NoScan-Simple-Dog-Diagnostic-v0"
    ;;
  v2core)
    readonly TASK_NAME="Isaac-Locomotion-V2-Core-Simple-Dog-Direct-v0"
    ;;
  v2robust)
    readonly TASK_NAME="Isaac-Locomotion-V2-Robust-Simple-Dog-Direct-v0"
    ;;
  v2goal)
    readonly TASK_NAME="Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-v0"
    ;;
  *)
    printf 'Invalid SIMPLE_DOG_TERRAIN: %s\n' "$terrain" >&2
    exit 2
    ;;
esac

mode="${1:-}"
case "$mode" in
  smoke)
    num_envs=128
    max_iterations=1
    ;;
  train)
    num_envs="${SIMPLE_DOG_NUM_ENVS:-512}"
    max_iterations="${SIMPLE_DOG_MAX_ITERATIONS:-500}"
    ;;
  *)
    printf 'Usage: %s smoke|train\n' "$0" >&2
    exit 2
    ;;
esac

run_id="$(date -u +%Y%m%dT%H%M%SZ)-${mode}-$$"
run_dir="${RUNS_ROOT}/${run_id}"
mkdir -p "$run_dir"

printf '%s\n' "$$" >"${run_dir}/launcher.pid"
printf 'running\n' >"${run_dir}/status"
printf '%s\n' "$TASK_NAME" >"${run_dir}/task"
printf '%s\n' "$num_envs" >"${run_dir}/num_envs"
printf '%s\n' "$max_iterations" >"${run_dir}/max_iterations"

on_signal() {
  printf 'interrupted\n' >"${run_dir}/status"
  exit 130
}
trap on_signal INT TERM

if [[ -n "${SIMPLE_DOG_CHECKPOINT:-}" ]]; then
  [[ "$SIMPLE_DOG_CHECKPOINT" == /workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/*.pth ||
     "$SIMPLE_DOG_CHECKPOINT" == /workspace/projects/training/logs/rl_games/simple_dog_rough_velocity_direct/*.pth ||
     "$SIMPLE_DOG_CHECKPOINT" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ]] || {
    printf 'Checkpoint is outside the simple-dog log directory: %s\n' "$SIMPLE_DOG_CHECKPOINT" >&2
    exit 2
  }
  if [[ "$terrain" == v2* ]]; then
    [[ "$SIMPLE_DOG_CHECKPOINT" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ]] || {
      printf 'V2 terrain requires a V2 checkpoint because policy observations differ.\n' >&2
      exit 2
    }
  else
    [[ "$SIMPLE_DOG_CHECKPOINT" != /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ]] || {
      printf 'A V2 checkpoint cannot be loaded into a V1 task.\n' >&2
      exit 2
    }
  fi
  [[ -f "$SIMPLE_DOG_CHECKPOINT" ]] || {
    printf 'Checkpoint does not exist: %s\n' "$SIMPLE_DOG_CHECKPOINT" >&2
    exit 2
  }
  printf '%s\n' "$SIMPLE_DOG_CHECKPOINT" >"${run_dir}/source_checkpoint"
fi

if [[ -n "${SIMPLE_DOG_TUNING_CONFIG:-}" ]]; then
  [[ "$SIMPLE_DOG_TUNING_CONFIG" == /workspace/projects/autoresearch/*.json ]] || {
    printf 'Tuning config is outside /workspace/projects/autoresearch: %s\n' \
      "$SIMPLE_DOG_TUNING_CONFIG" >&2
    exit 2
  }
  [[ -f "$SIMPLE_DOG_TUNING_CONFIG" ]] || {
    printf 'Tuning config does not exist: %s\n' "$SIMPLE_DOG_TUNING_CONFIG" >&2
    exit 2
  }
  # This validator is pure Python/stdlib. Running it through isaaclab.sh
  # needlessly initializes Isaac's bundled runtime immediately before the real
  # trainer and can leave the following SimulationContext.reset() wedged.
  /workspace/isaaclab/_isaac_sim/kit/python/bin/python3 \
    "${TRAINING_ROOT}/simple_dog_tuning.py" >"${run_dir}/effective_tuning.json"
  cp "$SIMPLE_DOG_TUNING_CONFIG" "${run_dir}/requested_tuning.json"
fi

set +e
PYTHONUNBUFFERED=1 \
PYTHONPATH="$TRAINING_ROOT" \
  /workspace/isaaclab/isaaclab.sh -p \
  "${TRAINING_ROOT}/train_simple_dog.py" \
  --task="$TASK_NAME" \
  --num_envs="$num_envs" \
  --max_iterations="$max_iterations" \
  --viz=none \
  >"${run_dir}/console.log" 2>&1
exit_code=$?
set -e

printf '%s\n' "$exit_code" >"${run_dir}/exit_code"
if [[ "$exit_code" -eq 0 ]] && grep -q "Training time:" "${run_dir}/console.log"; then
  printf 'complete\n' >"${run_dir}/status"
else
  printf 'failed\n' >"${run_dir}/status"
fi
exit "$exit_code"
