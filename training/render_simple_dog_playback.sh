#!/usr/bin/env bash
set -Eeuo pipefail

readonly ROOT="/workspace/projects/training"
checkpoint="${1:-}"
video_length="${2:-400}"
terrain="${3:-flat}"
control_profile="${4:-}"
task="Isaac-Velocity-Flat-Simple-Dog-Direct-Play-v0"
case "$terrain" in
  flat) task="Isaac-Velocity-Flat-Simple-Dog-Direct-Play-v0" ;;
  rough) task="Isaac-Velocity-Rough-Simple-Dog-Direct-Validation-v0" ;;
  v2core|v2robust|v2goal) task="Isaac-Locomotion-V2-Simple-Dog-Direct-Play-v0" ;;
  v2rough) task="Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-Play-v0" ;;
  *)
    printf 'Terrain must be flat, rough, v2core, v2robust, v2goal, or v2rough.\n' >&2
    exit 2
    ;;
esac

[[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_rough_velocity_direct/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_v2_*/*.pth ]] || {
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
if [[ -n "$control_profile" ]]; then
  [[ "$control_profile" == /workspace/projects/training/control_profiles/*.json ]] || {
    printf 'Control profile must be below the training control_profiles directory.\n' >&2
    exit 2
  }
  [[ -f "$control_profile" ]] || {
    printf 'Control profile does not exist: %s\n' "$control_profile" >&2
    exit 2
  }
  export SIMPLE_DOG_CONTROL_PROFILE="$control_profile"
fi

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
