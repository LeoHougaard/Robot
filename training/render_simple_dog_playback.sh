#!/usr/bin/env bash
set -Eeuo pipefail

readonly ROOT="/workspace/projects/training"
checkpoint="${1:-}"
video_length="${2:-400}"
terrain="${3:-flat}"
task="Isaac-Velocity-Flat-Simple-Dog-Direct-Play-v0"
[[ "$terrain" == "flat" || "$terrain" == "rough" ]] || {
  printf 'Terrain must be flat or rough.\n' >&2
  exit 2
}
[[ "$terrain" == "rough" ]] && task="Isaac-Velocity-Rough-Simple-Dog-Direct-Validation-v0"

[[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_rough_velocity_direct/*.pth ]] || {
  printf 'Checkpoint must be below the simple-dog log directory.\n' >&2
  exit 2
}
[[ -f "$checkpoint" ]] || {
  printf 'Checkpoint does not exist: %s\n' "$checkpoint" >&2
  exit 2
}
[[ "$video_length" =~ ^[0-9]+$ ]] && (( video_length >= 100 && video_length <= 3000 )) || {
  printf 'Video length must be 100-3000 policy steps.\n' >&2
  exit 2
}

experiment_dir="$(dirname "$(dirname "$checkpoint")")"
output_dir="${experiment_dir}/visual_validation"
mkdir -p "$output_dir"
printf 'running\n' >"${output_dir}/status"

on_exit() {
  exit_code=$?
  printf '%s\n' "$exit_code" >"${output_dir}/exit_code"
  if (( exit_code == 0 )); then
    printf 'complete\n' >"${output_dir}/status"
  else
    printf 'failed\n' >"${output_dir}/status"
  fi
}
trap on_exit EXIT

export PYTHONPATH="$ROOT"
export PYTHONUNBUFFERED=1
/workspace/isaaclab/isaaclab.sh -p "${ROOT}/play_simple_dog.py" \
  --task="$task" \
  --checkpoint="$checkpoint" \
  --num_envs=1 \
  --enable_cameras \
  --video \
  --video_length="$video_length" \
  --headless \
  >"${output_dir}/console.log" 2>&1
