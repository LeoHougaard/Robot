#!/usr/bin/env bash
set -Eeuo pipefail

readonly ROOT="/workspace/projects/training"
checkpoint="${1:-}"
stage="${2:-core}"
control_profile="${3:-}"
record_video="${4:-1}"
require_gait_quality="${5:-0}"
simulation_fit="${6:-}"

[[ "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_v2_*/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_v3_*/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_current_v3_rough_direct/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v17_*/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v18_*/*.pth ||
   "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v19_*/*.pth ]] || {
  printf 'Checkpoint must be a supported V2, CurrentV3, or CurrentBody checkpoint.\n' >&2
  exit 2
}
[[ -f "$checkpoint" ]] || { printf 'Checkpoint does not exist: %s\n' "$checkpoint" >&2; exit 2; }
if [[ -n "$control_profile" ]]; then
  [[ "$control_profile" == /workspace/projects/training/control_profiles/*.json && -f "$control_profile" ]] || {
    printf 'Control profile must exist below the training control_profiles directory.\n' >&2
    exit 2
  }
  export SIMPLE_DOG_CONTROL_PROFILE="$control_profile"
fi

evaluation_stage="$stage"
case "$stage" in
  core)
    task="Isaac-Locomotion-V2-Core-Simple-Dog-Direct-Eval-v0"
    video_length=900
    expected_segments=5
    screen_timeout=150s
    ;;
  robust)
    task="Isaac-Locomotion-V2-Robust-Simple-Dog-Direct-Eval-v0"
    video_length=900
    expected_segments=5
    screen_timeout=150s
    ;;
  goal)
    task="Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-Eval-v0"
    video_length=2300
    expected_segments=14
    screen_timeout=240s
    ;;
  rough)
    task="Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-Eval-v0"
    video_length=2300
    expected_segments=14
    screen_timeout=240s
    ;;
  current)
    task="Isaac-Locomotion-CurrentV3-Simple-Dog-Direct-Eval-v0"
    video_length=3550
    expected_segments=21
    screen_timeout=360s
    [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_v3_*/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_current_v3_rough_direct/*.pth ]] || {
      printf 'Current evaluation requires a CurrentV3 checkpoint.\n' >&2
      exit 2
    }
    [[ "$simulation_fit" == /workspace/projects/training/fits/*.json && -f "$simulation_fit" ]] || {
      printf 'Current evaluation requires its simulation fit below training/fits.\n' >&2
      exit 2
    }
    export SIMPLE_DOG_POLICY_FAMILY="current_v3"
    export SIMPLE_DOG_SIMULATION_FIT="$simulation_fit"
    ;;
  currentflat|currentstress)
    if [[ "$stage" == currentflat ]]; then
      task="Isaac-Locomotion-CurrentV3-Flat-Simple-Dog-Direct-Eval-v0"
    else
      task="Isaac-Locomotion-CurrentV3-Stress-Simple-Dog-Direct-Eval-v0"
    fi
    video_length=3550
    expected_segments=21
    screen_timeout=360s
    [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_v3_*/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_current_v3_rough_direct/*.pth ]] || {
      printf 'CurrentV3 evaluation requires a CurrentV3 checkpoint.\n' >&2
      exit 2
    }
    [[ "$simulation_fit" == /workspace/projects/training/fits/*.json && -f "$simulation_fit" ]] || {
      printf 'CurrentV3 evaluation requires its simulation fit below training/fits.\n' >&2
      exit 2
    }
    export SIMPLE_DOG_POLICY_FAMILY="current_v3"
    export SIMPLE_DOG_SIMULATION_FIT="$simulation_fit"
    ;;
  currentbodyv17|currentbodyv18|currentbodyv19|currentbodyv17push|currentbodyv18push|currentbodyv19push)
    version="${stage#currentbodyv}"
    if [[ "$version" == *push ]]; then
      version="${version%push}"
      task="Isaac-Locomotion-CurrentBodyV${version}-Simple-Dog-Direct-Push-Eval-v0"
    else
      task="Isaac-Locomotion-CurrentBodyV${version}-Simple-Dog-Direct-Eval-v0"
    fi
    video_length=2600
    expected_segments=14
    # CurrentBody startup can spend several minutes loading the large custom
    # articulation and checkpoint on the Spark. Keep evaluation bounded while
    # allowing the complete 52-second deterministic command screen to run.
    screen_timeout=600s
    [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v${version}_*/*.pth ]] || {
      printf 'CurrentBodyV%s evaluation requires a matching checkpoint.\n' "$version" >&2
      exit 2
    }
    [[ "$simulation_fit" == /workspace/projects/training/fits/*.json && -f "$simulation_fit" ]] || {
      printf 'CurrentBody evaluation requires its simulation fit below training/fits.\n' >&2
      exit 2
    }
    export SIMPLE_DOG_POLICY_FAMILY="current_body_v${version}"
    export SIMPLE_DOG_SIMULATION_FIT="$simulation_fit"
    evaluation_stage="currentbody"
    ;;
  *) printf 'Unsupported evaluation stage: %s\n' "$stage" >&2; exit 2 ;;
esac

if [[ "$stage" != current && "$stage" != currentflat && "$stage" != currentstress &&
      "$stage" != currentbodyv17 && "$stage" != currentbodyv18 &&
      "$stage" != currentbodyv19 && "$stage" != currentbodyv17push &&
      "$stage" != currentbodyv18push && "$stage" != currentbodyv19push &&
      -n "$simulation_fit" ]]; then
  printf 'A simulation fit may be supplied only for current-aware evaluation.\n' >&2
  exit 2
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
checkpoint_dir="$(dirname "$(dirname "$checkpoint")")"
output_dir="${checkpoint_dir}/evaluation/${timestamp}-${stage}"
mkdir -p "$output_dir"
printf 'running\n' >"$output_dir/status"

on_exit() {
  exit_code=$?
  printf '%s\n' "$exit_code" >"$output_dir/exit_code"
  if (( exit_code == 0 )); then
    printf 'passed\n' >"$output_dir/status"
  else
    printf 'failed\n' >"$output_dir/status"
  fi
  printf 'Evaluation evidence: %s\n' "$output_dir"
}
trap on_exit EXIT

export PYTHONPATH="$ROOT"
export PYTHONUNBUFFERED=1
play_args=(
  -p "${ROOT}/play_simple_dog.py"
  --task="$task" \
  --checkpoint="$checkpoint" \
  --num_envs=1 \
  --seed=42 \
  --deterministic
  --headless
)
if [[ "$record_video" == "1" ]]; then
  play_args+=(--enable_cameras --video --video_length="$video_length")
elif [[ "$record_video" != "0" ]]; then
  printf 'record_video must be 0 or 1.\n' >&2
  exit 2
fi
if [[ "$record_video" == "1" ]]; then
  /workspace/isaaclab/isaaclab.sh "${play_args[@]}" \
    >"$output_dir/console.log" 2>&1
else
  # The RL-Games player otherwise runs forever without Gym's video wrapper.
  # Stop it after a bounded screen and accept the timeout only when every
  # deterministic segment has already emitted its complete metrics record.
  set +e
  # GB10 Kit startup can spend close to a minute loading plugins after a
  # preceding Isaac process exits. Keep the rollout bounded, but leave enough
  # room for startup plus the complete deterministic command screen.
  timeout -k 10s "$screen_timeout" /workspace/isaaclab/isaaclab.sh "${play_args[@]}" \
    >"$output_dir/console.log" 2>&1
  play_exit=$?
  set -e
  segment_count="$(grep -c '^EVAL_SEGMENT ' "$output_dir/console.log" || true)"
  if (( segment_count < expected_segments )); then
    printf 'Screen ended with %s/%s required evaluation segments (player exit %s).\n' \
      "$segment_count" "$expected_segments" "$play_exit" >&2
    exit 1
  fi
fi

if [[ "$require_gait_quality" != "0" && "$require_gait_quality" != "1" ]]; then
  printf 'require_gait_quality must be 0 or 1.\n' >&2
  exit 2
fi
quality_args=()
if [[ "$stage" == "rough" || "$stage" == current* || "$require_gait_quality" == "1" ]]; then
  quality_args+=(--require-gait-quality)
fi
/workspace/isaaclab/isaaclab.sh -p "${ROOT}/evaluate_simple_dog_policy.py" \
  "$output_dir/console.log" --stage "$evaluation_stage" --output "$output_dir/result.json" \
  "${quality_args[@]}"
