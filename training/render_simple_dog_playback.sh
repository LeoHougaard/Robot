#!/usr/bin/env bash
set -Eeuo pipefail

readonly ROOT="/workspace/projects/training"
checkpoint="${1:-}"
video_length="${2:-400}"
terrain="${3:-flat}"
control_profile="${4:-}"
simulation_fit="${5:-}"
task="Isaac-Velocity-Flat-Simple-Dog-Direct-Play-v0"
review_num_envs="${SIMPLE_DOG_REVIEW_NUM_ENVS:-1}"
case "$terrain" in
  flat) task="Isaac-Velocity-Flat-Simple-Dog-Direct-Play-v0" ;;
  rough) task="Isaac-Velocity-Rough-Simple-Dog-Direct-Validation-v0" ;;
  v2core|v2robust|v2goal) task="Isaac-Locomotion-V2-Simple-Dog-Direct-Play-v0" ;;
  v2rough) task="Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-Play-v0" ;;
  currentv3*)
    task="Isaac-Locomotion-CurrentV3-Simple-Dog-Direct-Play-v0"
    export SIMPLE_DOG_POLICY_FAMILY="current_v3"
    [[ "$simulation_fit" == /workspace/projects/training/fits/*.json && -f "$simulation_fit" ]] || {
      printf 'CurrentV3 playback requires its simulation fit below training/fits.\n' >&2
      exit 2
    }
    export SIMPLE_DOG_SIMULATION_FIT="$simulation_fit"
    ;;
  currentbodyv4hard)
    task="Isaac-Locomotion-CurrentBodyV4-Simple-Dog-Direct-Play-v0"
    export SIMPLE_DOG_POLICY_FAMILY="current_body_v4"
    [[ "$simulation_fit" == /workspace/projects/training/fits/*.json && -f "$simulation_fit" ]] || {
      printf 'CurrentBodyV4 playback requires its simulation fit below training/fits.\n' >&2
      exit 2
    }
    export SIMPLE_DOG_SIMULATION_FIT="$simulation_fit"
    review_num_envs="${SIMPLE_DOG_REVIEW_NUM_ENVS:-5}"
    ;;
  currentbodyv5hard)
    task="Isaac-Locomotion-CurrentBodyV5-Simple-Dog-Direct-Play-v0"
    export SIMPLE_DOG_POLICY_FAMILY="current_body_v5"
    [[ "$simulation_fit" == /workspace/projects/training/fits/*.json && -f "$simulation_fit" ]] || {
      printf 'CurrentBodyV5 playback requires its simulation fit below training/fits.\n' >&2
      exit 2
    }
    export SIMPLE_DOG_SIMULATION_FIT="$simulation_fit"
    review_num_envs="${SIMPLE_DOG_REVIEW_NUM_ENVS:-5}"
    ;;
  *)
    printf 'Terrain must be a supported V1, V2, CurrentV3, CurrentBodyV4, or CurrentBodyV5 stage.\n' >&2
    exit 2
    ;;
esac

[[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_rough_velocity_direct/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_v2_*/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_current_v3_rough_direct/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_v3_*/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v4_*/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v5_*/*.pth ]] || {
  printf 'Checkpoint must be below the simple-dog log directory.\n' >&2
  exit 2
}
if [[ "$terrain" != currentv3* && "$terrain" != currentbodyv4hard &&
      "$terrain" != currentbodyv5hard && -n "$simulation_fit" ]]; then
  printf 'A simulation fit may be supplied only for current-aware playback.\n' >&2
  exit 2
fi
[[ -f "$checkpoint" ]] || {
  printf 'Checkpoint does not exist: %s\n' "$checkpoint" >&2
  exit 2
}
[[ "$video_length" =~ ^[0-9]+$ ]] && (( video_length >= 100 && video_length <= 3000 )) || {
  printf 'Video length must be 100-3000 policy steps.\n' >&2
  exit 2
}
[[ "$review_num_envs" =~ ^[0-9]+$ ]] && (( review_num_envs >= 1 && review_num_envs <= 64 )) || {
  printf 'Review environment count must be 1-64.\n' >&2
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
checkpoint_epoch="$(basename "$checkpoint" | sed -nE 's/.*_ep_([0-9]+)_.*/\1/p')"
if [[ -n "${SIMPLE_DOG_VALIDATION_SAMPLE:-}" ]]; then
  sample_index="$SIMPLE_DOG_VALIDATION_SAMPLE"
elif [[ -n "$checkpoint_epoch" ]]; then
  sample_index="$(( (checkpoint_epoch / 25) % 5 ))"
else
  sample_index=0
fi
[[ "$sample_index" =~ ^[0-4]$ ]] || {
  printf 'Validation sample must be 0-4.\n' >&2
  exit 2
}
export SIMPLE_DOG_VALIDATION_SAMPLE="$sample_index"
mkdir -p "$output_dir"
printf 'running\n' >"${output_dir}/status"
printf '%s\n' "$sample_index" >"${output_dir}/sample_index"
printf 'Validation sample: %s/5\n' "$((sample_index + 1))"
if [[ -n "$checkpoint_epoch" ]]; then
  printf 'Checkpoint epoch: %s\n' "$checkpoint_epoch"
fi
printf 'Review environments: %s\n' "$review_num_envs"

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
  --num_envs="$review_num_envs" \
  --enable_cameras \
  --video \
  --video_length="$video_length" \
  --headless \
  >"${output_dir}/console.log" 2>&1
