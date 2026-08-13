#!/usr/bin/env bash
set -Eeuo pipefail

readonly CONTAINER="isaac-lab-gb10"
readonly ROOT="/home/leo/isaac-workspace/projects/training"
readonly RUNS_ROOT="${ROOT}/runs/simple_dog"

active() {
  docker exec "$CONTAINER" pgrep -f '[t]rain_simple_dog.py' >/dev/null 2>&1
}

latest_run() {
  find "$RUNS_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null |
    sort |
    tail -1
}

latest_video() {
  local video
  # Deterministic promotion may select a retained checkpoint from an earlier
  # run. Search all V2 experiment videos by modification time so Fetch newest
  # returns the rollout that was actually recorded most recently instead of
  # being pinned to the newest training-run directory.
  video="$(find "${ROOT}/logs/rl_games" -type f -name '*.mp4' -size +1024c \
    -path '*/quadruped_v2_*/videos/*' -printf '%T@ %p\n' 2>/dev/null | \
    sort -n | tail -1 | cut -d' ' -f2- || true)"
  [[ -z "$video" ]] || printf '%s\n' "$video"
}

render_latest_video() {
  local video_length="${1:-400}"
  local latest experiment output_root checkpoint container_checkpoint task terrain
  local profile_id profile_sha control_profile
  [[ "$video_length" =~ ^[0-9]+$ ]] && ((video_length >= 100 && video_length <= 3000)) || {
    printf 'Video length must be 100-3000 policy steps.\n' >&2
    return 2
  }
  if active; then
    printf 'Training is still running. A rollout can be rendered as soon as PPO releases Isaac Sim.\n' >&2
    return 3
  fi
  latest="$(latest_run || true)"
  [[ -n "$latest" ]] || { printf 'No training run is available to render.\n' >&2; return 2; }
  experiment="$(
    grep -E 'Exact experiment name requested from command line: /workspace/projects/' \
      "$latest/console.log" 2>/dev/null | tail -1 | sed 's/.*: //' || true
  )"
  [[ "$experiment" == /workspace/projects/training/logs/rl_games/* ]] || {
    printf 'The latest run has not created an RL-Games experiment yet.\n' >&2
    return 2
  }
  output_root="${ROOT}/${experiment#/workspace/projects/training/}"
  checkpoint="$(find "$output_root/nn" -maxdepth 1 -type f -name '*.pth' ! -name 'last_*' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2- || true)"
  if [[ -z "$checkpoint" ]]; then
    checkpoint="$(find "$output_root/nn" -maxdepth 1 -type f -name 'last_*.pth' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2- || true)"
  fi
  [[ -n "$checkpoint" ]] || { printf 'The latest run has no checkpoint to render.\n' >&2; return 2; }
  container_checkpoint="${experiment}/nn/$(basename "$checkpoint")"
  task="$(cat "$latest/task" 2>/dev/null || true)"
  case "$task" in
    Isaac-Velocity-Flat-*) terrain="flat" ;;
    Isaac-Velocity-Rough-*) terrain="rough" ;;
    Isaac-Locomotion-V2-Core-*) terrain="v2core" ;;
    Isaac-Locomotion-V2-Robust-*) terrain="v2robust" ;;
    Isaac-Locomotion-V2-Goal-*) terrain="v2goal" ;;
    Isaac-Locomotion-V2-Rough-*) terrain="v2rough" ;;
    *) printf 'Unsupported task for rollout rendering: %s\n' "$task" >&2; return 2 ;;
  esac
  control_profile=""
  profile_id="$(cat "$latest/profile_id" 2>/dev/null || true)"
  if [[ -n "$profile_id" ]]; then
    [[ "$profile_id" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]] || {
      printf 'Invalid profile id in the latest run.\n' >&2
      return 2
    }
    profile_sha="$(cat "$latest/profile_sha" 2>/dev/null || true)"
    [[ "$profile_sha" =~ ^[a-f0-9]{64}$ ]] || {
      printf 'Invalid profile SHA in the latest run.\n' >&2
      return 2
    }
    control_profile="/workspace/projects/training/control_profiles/${profile_id}-${profile_sha:0:12}.json"
    docker exec "$CONTAINER" test -f "$control_profile" || {
      printf 'The latest run control profile is not deployed: %s\n' "$control_profile" >&2
      return 2
    }
  fi
  docker exec \
    --workdir /workspace/isaaclab \
    "$CONTAINER" \
    /workspace/projects/training/render_simple_dog_playback.sh \
      "$container_checkpoint" "$video_length" "$terrain" "$control_profile"
}

start_training() {
  local num_envs="${1:-512}"
  local max_iterations="${2:-500}"
  local checkpoint="${3:-}"
  local tuning_config="${4:-}"
  local terrain="${5:-flat}"
  local control_profile="${6:-}"
  local profile_sha="${7:-}"
  local record_video="${8:-0}"
  local video_interval="${9:-5000}"
  local video_length="${10:-400}"
  local before latest

  [[ "$num_envs" =~ ^[0-9]+$ ]] && ((num_envs >= 128 && num_envs <= 16384)) ||
    { printf 'Invalid environment count: %s\n' "$num_envs" >&2; exit 2; }
  [[ "$max_iterations" =~ ^[0-9]+$ ]] && ((max_iterations >= 1 && max_iterations <= 100000)) ||
    { printf 'Invalid iteration count: %s\n' "$max_iterations" >&2; exit 2; }
  [[ "$terrain" == flat || "$terrain" == rough || "$terrain" == rough_noscan ||
     "$terrain" == v2core || "$terrain" == v2robust ||
     "$terrain" == v2goal || "$terrain" == v2rough ]] ||
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
       "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_v2_*/*.pth ]] ||
      { printf 'Checkpoint is outside the simple-dog log directory: %s\n' "$checkpoint" >&2; exit 2; }
    if [[ "$terrain" == v2* ]]; then
      [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ||
         "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_v2_*/*.pth ]] ||
        { printf 'V2 terrain requires a V2 checkpoint because policy observations differ.\n' >&2; exit 2; }
    else
      [[ "$checkpoint" != /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth &&
         "$checkpoint" != /workspace/projects/training/logs/rl_games/quadruped_v2_*/*.pth ]] ||
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
  if [[ -n "$control_profile" ]]; then
    [[ "$control_profile" == /workspace/projects/training/control_profiles/*.json ]] ||
      { printf 'Control profile is outside the training control_profiles directory: %s\n' "$control_profile" >&2; exit 2; }
    [[ "$profile_sha" =~ ^[a-f0-9]{64}$ ]] ||
      { printf 'Invalid control profile SHA-256.\n' >&2; exit 2; }
    docker exec "$CONTAINER" test -f "$control_profile" ||
      { printf 'Control profile does not exist: %s\n' "$control_profile" >&2; exit 2; }
  fi
  [[ "$record_video" == 0 || "$record_video" == 1 ]] ||
    { printf 'Invalid record-video flag.\n' >&2; exit 2; }
  [[ "$video_interval" =~ ^[0-9]+$ ]] && ((video_interval >= 100 && video_interval <= 10000000)) ||
    { printf 'Invalid video interval.\n' >&2; exit 2; }
  [[ "$video_length" =~ ^[0-9]+$ ]] && ((video_length >= 50 && video_length <= 5000)) ||
    { printf 'Invalid video length.\n' >&2; exit 2; }

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
    -e "SIMPLE_DOG_CONTROL_PROFILE=${control_profile}" \
    -e "SIMPLE_DOG_CONTROL_PROFILE_SHA=${profile_sha}" \
    -e "SIMPLE_DOG_RECORD_VIDEO=${record_video}" \
    -e "SIMPLE_DOG_VIDEO_INTERVAL=${video_interval}" \
    -e "SIMPLE_DOG_VIDEO_LENGTH=${video_length}" \
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
  local container latest status progress reward gpu experiment surface

  container="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || printf 'absent')"
  printf 'Container: %s\n' "$container"
  latest="$(latest_run || true)"
  if [[ -z "$latest" ]]; then
    printf 'Training:  no runs found\n'
    return 0
  fi

  status="$(cat "$latest/status" 2>/dev/null || printf 'unknown')"
  if [[ "$status" == running ]] && ! active; then
    if [[ "$container" == running ]]; then
      status="idle (previous run interrupted; no trainer process)"
    else
      status="interrupted (container is not running; launcher state was stale)"
    fi
  fi
  printf 'Training:  %s\n' "$status"
  printf 'Run data:  %s\n' "$latest"
  [[ ! -f "$latest/num_envs" ]] || { printf 'Envs:      '; cat "$latest/num_envs"; }
  [[ ! -f "$latest/max_iterations" ]] || { printf 'Target:    '; cat "$latest/max_iterations"; }
  [[ ! -f "$latest/profile_id" ]] || { printf 'Profile:   '; cat "$latest/profile_id"; }
  [[ ! -f "$latest/profile_sha" ]] || { printf 'Profile SHA: '; cat "$latest/profile_sha"; }
  [[ ! -f "$latest/task" ]] || { printf 'Task:      '; cat "$latest/task"; }
  if [[ -f "$latest/control_profile.json" ]]; then
    surface="$({
      sed -n 's/.*"surface"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$latest/control_profile.json" | head -1
    } || true)"
    [[ -z "$surface" ]] || printf 'Surface:   %s\n' "$surface"
  fi

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
    start_training "${2:-512}" "${3:-500}" "${4:-}" "${5:-}" "${6:-flat}" "${7:-}" "${8:-}" "${9:-0}" "${10:-5000}" "${11:-400}"
    ;;
  status)
    status_training
    ;;
  latest-video)
    latest_video
    ;;
  render-latest-video)
    render_latest_video "${2:-400}"
    ;;
  *)
    printf 'Usage: %s active|start [num-envs max-iterations checkpoint tuning-config terrain control-profile profile-sha record-video video-interval video-length]|status|latest-video|render-latest-video [video-length]\n' "$0" >&2
    exit 2
    ;;
esac
