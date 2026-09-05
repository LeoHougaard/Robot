#!/usr/bin/env bash
set -Eeuo pipefail

readonly CONTAINER="isaac-lab-gb10"
readonly REVIEW_CONTAINER="isaac-lab-gb10-review"
readonly ROOT="/home/leo/isaac-workspace/projects/training"
readonly RUNS_ROOT="${ROOT}/runs/simple_dog"

active() {
  docker exec "$CONTAINER" pgrep -f '[t]rain_simple_dog.py' >/dev/null 2>&1
}

clear_stale_hub_lock() {
  # Isaac's camera recorder can leave this zero-byte lock behind after Kit has
  # exited. A later SimulationApp startup then waits indefinitely even though
  # no Hub process exists. Remove only the current container user's lock, and
  # only after verifying that no Hub process is alive.
  docker exec --user 0 "$CONTAINER" sh -c '
    # Match executable names, not arbitrary command-line text containing the
    # lock path. Wrapper commands made the previous substring check see itself.
    if ! pgrep -x omni.hub >/dev/null 2>&1 &&
       ! pgrep -x omni-hub >/dev/null 2>&1 &&
       ! pgrep -x hub >/dev/null 2>&1 &&
       ! pgrep -x hub-daemon >/dev/null 2>&1; then
      # The container is configured to run as leo, while the bundled Isaac
      # runtime inherits the image account name isaac-sim and uses that lock.
      # Remove only these two exact per-user lock files.
      rm -f -- /tmp/hub-leo.lock /tmp/hub-isaac-sim.lock
    fi
  '
}

run_rollout_renderer() {
  local checkpoint="$1"
  local video_length="$2"
  local terrain="$3"
  local control_profile="$4"
  local simulation_fit="$5"
  local sample_index="$6"
  local image container_user

  if active; then
    # Isaac Sim cannot be started twice in the training container. Use the
    # same immutable image and mounted project in a short-lived container so
    # checkpoint review does not pause or mutate the scratch training job.
    if docker inspect "$REVIEW_CONTAINER" >/dev/null 2>&1; then
      printf 'A checkpoint review is already running. Wait for it to finish.\n' >&2
      return 3
    fi
    image="$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")"
    container_user="$(docker inspect -f '{{.Config.User}}' "$CONTAINER")"
    [[ -n "$image" ]] || {
      printf 'Could not resolve the Isaac Lab image for checkpoint review.\n' >&2
      return 1
    }
    [[ -n "$container_user" ]] || {
      printf 'Could not resolve the Isaac Lab user for checkpoint review.\n' >&2
      return 1
    }
    docker run --rm \
      --name "$REVIEW_CONTAINER" \
      --gpus all \
      --shm-size 16g \
      --volumes-from "$CONTAINER" \
      --user "$container_user" \
      --workdir /workspace/isaaclab \
      --entrypoint /bin/bash \
      -e "ACCEPT_EULA=Y" \
      -e "SIMPLE_DOG_VALIDATION_SAMPLE=${sample_index}" \
      -e "SIMPLE_DOG_REVIEW_NUM_ENVS=5" \
      "$image" \
      /workspace/projects/training/render_simple_dog_playback.sh \
        "$checkpoint" "$video_length" "$terrain" "$control_profile" "$simulation_fit"
  else
    clear_stale_hub_lock
    docker exec \
      --workdir /workspace/isaaclab \
      -e "SIMPLE_DOG_VALIDATION_SAMPLE=${sample_index}" \
      -e "SIMPLE_DOG_REVIEW_NUM_ENVS=5" \
      "$CONTAINER" \
      /workspace/projects/training/render_simple_dog_playback.sh \
        "$checkpoint" "$video_length" "$terrain" "$control_profile" "$simulation_fit"
  fi
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
    \( -path '*/quadruped_v2_*/videos/*' \
       -o -path '*/quadruped_current_v3_*/videos/*' \
       -o -path '*/quadruped_current_body_v4_*/videos/*' \
       -o -path '*/quadruped_current_body_v5_*/videos/*' \
       -o -path '*/quadruped_current_body_v6_*/videos/*' \
       -o -path '*/quadruped_current_body_v7_*/videos/*' \
       -o -path '*/quadruped_current_body_v8_*/videos/*' \
       -o -path '*/quadruped_current_body_v9_*/videos/*' \
       -o -path '*/quadruped_current_body_v10_*/videos/*' \
       -o -path '*/quadruped_current_body_v11_*/videos/*' \
       -o -path '*/quadruped_current_body_v12_*/videos/*' \
       -o -path '*/quadruped_current_body_v13_*/videos/*' \
       -o -path '*/quadruped_current_body_v14_*/videos/*' \
       -o -path '*/quadruped_current_body_v15_*/videos/*' \
       -o -path '*/quadruped_current_body_v16_*/videos/*' \
       -o -path '*/quadruped_current_body_v17_*/videos/*' \
       -o -path '*/quadruped_current_body_v18_*/videos/*' \
       -o -path '*/quadruped_current_body_v19_*/videos/*' \
       -o -path '*/simple_dog_current_v3_rough_direct/videos/*' \) \
    -printf '%T@ %p\n' 2>/dev/null | \
    sort -n | tail -1 | cut -d' ' -f2- || true)"
  [[ -z "$video" ]] || printf '%s\n' "$video"
}

render_latest_video() {
  local video_length="${1:-400}"
  local sample_index="${2:-}"
  local latest experiment output_root checkpoint container_checkpoint task terrain candidate
  local -a candidates
  local profile_id profile_sha control_profile simulation_fit simulation_fit_host simulation_fit_sha
  latest=""
  checkpoint=""
  [[ "$video_length" =~ ^[0-9]+$ ]] && ((video_length >= 100 && video_length <= 3000)) || {
    printf 'Video length must be 100-3000 policy steps.\n' >&2
    return 2
  }
  if [[ -z "$sample_index" ]]; then
    sample_index="$(( RANDOM % 5 ))"
  fi
  [[ "$sample_index" =~ ^[0-4]$ ]] || {
    printf 'Validation sample must be 0-4.\n' >&2
    return 2
  }
  if active; then
    # An active review must belong to the active run. If that run has not
    # emitted a checkpoint yet, fail honestly instead of rendering an older
    # experiment and rejecting it only after spending several GPU minutes.
    latest="$(latest_run || true)"
    candidates=()
    [[ -z "$latest" ]] || candidates+=("$latest")
    latest=""
  else
    mapfile -t candidates < <(
      find "$RUNS_ROOT" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort -r
    )
  fi
  for candidate in "${candidates[@]}"; do
    experiment="$(
      grep -E 'Exact experiment name requested from command line: /workspace/projects/' \
        "$candidate/console.log" 2>/dev/null | tail -1 | sed 's/.*: //' || true
    )"
    [[ "$experiment" == /workspace/projects/training/logs/rl_games/* ]] || continue
    output_root="${ROOT}/${experiment#/workspace/projects/training/}"
    # "Fetch newest" means newest retained policy, including the periodic
    # last_<experiment>_ep_<N> snapshots. A best-reward file can be older than
    # the latest epoch and must not silently win merely because of its name.
    checkpoint="$(find "$output_root/nn" -maxdepth 1 -type f -name '*.pth' -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2- || true)"
    if [[ -n "$checkpoint" ]]; then
      latest="$candidate"
      break
    fi
  done
  [[ -n "$latest" ]] || {
    printf 'No training run with a renderable checkpoint is available.\n' >&2
    return 2
  }
  container_checkpoint="${experiment}/nn/$(basename "$checkpoint")"
  task="$(cat "$latest/task" 2>/dev/null || true)"
  case "$task" in
    Isaac-Velocity-Flat-*) terrain="flat" ;;
    Isaac-Velocity-Rough-*) terrain="rough" ;;
    Isaac-Locomotion-V2-Core-*) terrain="v2core" ;;
    Isaac-Locomotion-V2-Robust-*) terrain="v2robust" ;;
    Isaac-Locomotion-V2-Goal-*) terrain="v2goal" ;;
    Isaac-Locomotion-V2-Rough-*) terrain="v2rough" ;;
    Isaac-Locomotion-CurrentV3-Core-*) terrain="currentv3core" ;;
    Isaac-Locomotion-CurrentV3-Forward-Specialist-*) terrain="currentv3forwardspecialist" ;;
    Isaac-Locomotion-CurrentV3-Reverse-Specialist-*) terrain="currentv3reversespecialist" ;;
    Isaac-Locomotion-CurrentV3-Reverse-*) terrain="currentv3reverse" ;;
    Isaac-Locomotion-CurrentV3-Strafe-*) terrain="currentv3strafe" ;;
    Isaac-Locomotion-CurrentV3-Turn-*) terrain="currentv3turn" ;;
    Isaac-Locomotion-CurrentV3-Goal-*) terrain="currentv3goal" ;;
    Isaac-Locomotion-CurrentV3-Posture-*) terrain="currentv3posture" ;;
    Isaac-Locomotion-CurrentV3-*) terrain="currentv3rough" ;;
    Isaac-Locomotion-CurrentBodyV4-*) terrain="currentbodyv4hard" ;;
    Isaac-Locomotion-CurrentBodyV5-*) terrain="currentbodyv5hard" ;;
    Isaac-Locomotion-CurrentBodyV6-*) terrain="currentbodyv6hard" ;;
    Isaac-Locomotion-CurrentBodyV7-*) terrain="currentbodyv7hard" ;;
    Isaac-Locomotion-CurrentBodyV8-*) terrain="currentbodyv8hard" ;;
    Isaac-Locomotion-CurrentBodyV9-*) terrain="currentbodyv9hard" ;;
    Isaac-Locomotion-CurrentBodyV10-*) terrain="currentbodyv10hard" ;;
    Isaac-Locomotion-CurrentBodyV11-*) terrain="currentbodyv11hard" ;;
    Isaac-Locomotion-CurrentBodyV12-*) terrain="currentbodyv12hard" ;;
    Isaac-Locomotion-CurrentBodyV13-*) terrain="currentbodyv13hard" ;;
    Isaac-Locomotion-CurrentBodyV14-*) terrain="currentbodyv14hard" ;;
    Isaac-Locomotion-CurrentBodyV15-*) terrain="currentbodyv15hard" ;;
    Isaac-Locomotion-CurrentBodyV16-*) terrain="currentbodyv16hard" ;;
    Isaac-Locomotion-CurrentBodyV17-*) terrain="currentbodyv17hard" ;;
    Isaac-Locomotion-CurrentBodyV18-*) terrain="currentbodyv18hard" ;;
    Isaac-Locomotion-CurrentBodyV19-*) terrain="currentbodyv19hard" ;;
    *) printf 'Unsupported task for rollout rendering: %s\n' "$task" >&2; return 2 ;;
  esac
  control_profile=""
  simulation_fit=""
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
  if [[ "$terrain" == currentbodyv4hard || "$terrain" == currentbodyv5hard ||
        "$terrain" == currentbodyv6hard || "$terrain" == currentbodyv7hard ||
        "$terrain" == currentbodyv8hard || "$terrain" == currentbodyv9hard ||
        "$terrain" == currentbodyv10hard || "$terrain" == currentbodyv11hard ||
        "$terrain" == currentbodyv12hard || "$terrain" == currentbodyv13hard ||
        "$terrain" == currentbodyv14hard || "$terrain" == currentbodyv15hard ||
        "$terrain" == currentbodyv16hard || "$terrain" == currentbodyv17hard ||
        "$terrain" == currentbodyv18hard || "$terrain" == currentbodyv19hard ]]; then
    simulation_fit_sha="$(cat "$latest/simulation_fit_sha" 2>/dev/null || true)"
    [[ "$simulation_fit_sha" =~ ^[a-f0-9]{64}$ ]] || {
      printf 'Invalid simulation-fit SHA in the latest current-body run.\n' >&2
      return 2
    }
    simulation_fit_host="$(find "${ROOT}/fits" -maxdepth 1 -type f \
      -name "current-body-*-${simulation_fit_sha:0:12}.json" -print -quit 2>/dev/null || true)"
    [[ -n "$simulation_fit_host" ]] || {
      printf 'The latest run simulation fit is not deployed for SHA %s.\n' \
        "$simulation_fit_sha" >&2
      return 2
    }
    simulation_fit="/workspace/projects/training/fits/$(basename "$simulation_fit_host")"
    docker exec "$CONTAINER" test -f "$simulation_fit" || {
      printf 'The latest run simulation fit is not deployed: %s\n' "$simulation_fit" >&2
      return 2
    }
  fi
  run_rollout_renderer \
    "$container_checkpoint" "$video_length" "$terrain" \
    "$control_profile" "$simulation_fit" "$sample_index"
}

render_checkpoint_video() {
  local checkpoint="${1:-}"
  local video_length="${2:-400}"
  local terrain="${3:-v2rough}"
  local control_profile="${4:-}"
  local sample_index="${5:-0}"
  local simulation_fit="${6:-}"
  local host_checkpoint experiment_root video
  [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_rough_velocity_direct/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_v2_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_current_v3_rough_direct/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_v3_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v4_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v5_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v6_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v7_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v8_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v9_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v10_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v11_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v12_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v13_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v14_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v15_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v16_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v17_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v18_*/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v19_*/*.pth ]] || {
    printf 'Checkpoint must be below the simple-dog log directory.\n' >&2
    return 2
  }
  [[ "$terrain" == flat || "$terrain" == rough || "$terrain" == v2core ||
     "$terrain" == v2robust || "$terrain" == v2goal || "$terrain" == v2rough ||
     "$terrain" == currentv3* || "$terrain" == currentbodyv4hard ||
     "$terrain" == currentbodyv5hard || "$terrain" == currentbodyv6hard ||
     "$terrain" == currentbodyv7hard || "$terrain" == currentbodyv8hard ||
     "$terrain" == currentbodyv9hard || "$terrain" == currentbodyv10hard ||
     "$terrain" == currentbodyv11hard || "$terrain" == currentbodyv12hard ||
     "$terrain" == currentbodyv13hard || "$terrain" == currentbodyv14hard ||
     "$terrain" == currentbodyv15hard || "$terrain" == currentbodyv16hard ||
     "$terrain" == currentbodyv17hard || "$terrain" == currentbodyv18hard ||
     "$terrain" == currentbodyv19hard ]] || {
    printf 'Unsupported review terrain: %s\n' "$terrain" >&2
    return 2
  }
  if [[ "$terrain" == currentv3* || "$terrain" == currentbodyv4hard ||
        "$terrain" == currentbodyv5hard || "$terrain" == currentbodyv6hard ||
        "$terrain" == currentbodyv7hard || "$terrain" == currentbodyv8hard ||
        "$terrain" == currentbodyv9hard || "$terrain" == currentbodyv10hard ||
        "$terrain" == currentbodyv11hard || "$terrain" == currentbodyv12hard ||
        "$terrain" == currentbodyv13hard || "$terrain" == currentbodyv14hard ||
        "$terrain" == currentbodyv15hard || "$terrain" == currentbodyv16hard ||
        "$terrain" == currentbodyv17hard || "$terrain" == currentbodyv18hard ||
        "$terrain" == currentbodyv19hard ]]; then
    [[ "$simulation_fit" == /workspace/projects/training/fits/*.json ]] || {
      printf 'Current-aware review requires its simulation fit.\n' >&2
      return 2
    }
    docker exec "$CONTAINER" test -f "$simulation_fit" || {
      printf 'Simulation fit does not exist: %s\n' "$simulation_fit" >&2
      return 2
    }
  elif [[ -n "$simulation_fit" ]]; then
    printf 'A simulation fit may be supplied only for current-aware review.\n' >&2
    return 2
  fi
  [[ "$sample_index" =~ ^[0-4]$ ]] || {
    printf 'Validation sample must be 0-4.\n' >&2
    return 2
  }
  host_checkpoint="${ROOT}/${checkpoint#/workspace/projects/training/}"
  [[ -f "$host_checkpoint" ]] || {
    printf 'Checkpoint does not exist: %s\n' "$checkpoint" >&2
    return 2
  }
  if [[ -n "$control_profile" ]]; then
    [[ "$control_profile" == /workspace/projects/training/control_profiles/*.json ]] || {
      printf 'Control profile must be below the training control_profiles directory.\n' >&2
      return 2
    }
    docker exec "$CONTAINER" test -f "$control_profile" || {
      printf 'Control profile does not exist: %s\n' "$control_profile" >&2
      return 2
    }
  fi
  run_rollout_renderer \
    "$checkpoint" "$video_length" "$terrain" \
    "$control_profile" "$simulation_fit" "$sample_index"
  experiment_root="$(dirname "$(dirname "$host_checkpoint")")"
  video="$(find "$experiment_root/videos" -type f -name '*.mp4' -size +1024c -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2- || true)"
  [[ -n "$video" ]] || {
    printf 'Rendering completed but no rollout video was found.\n' >&2
    return 1
  }
  printf 'Rendered video: %s\n' "$video"
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
  local simulation_fit="${11:-}"
  local seed_override="${12:-}"
  local simulation_fit_sha=""
  local before latest

  [[ "$num_envs" =~ ^[0-9]+$ ]] && ((num_envs >= 128 && num_envs <= 16384)) ||
    { printf 'Invalid environment count: %s\n' "$num_envs" >&2; exit 2; }
  [[ "$max_iterations" =~ ^[0-9]+$ ]] && ((max_iterations >= 1 && max_iterations <= 100000)) ||
    { printf 'Invalid iteration count: %s\n' "$max_iterations" >&2; exit 2; }
  [[ "$terrain" == flat || "$terrain" == rough || "$terrain" == rough_noscan ||
     "$terrain" == v2core || "$terrain" == v2robust ||
     "$terrain" == v2goal || "$terrain" == v2rough ||
     "$terrain" == currentv3* || "$terrain" == currentbodyv4hard ||
     "$terrain" == currentbodyv5hard || "$terrain" == currentbodyv6hard ||
     "$terrain" == currentbodyv7hard || "$terrain" == currentbodyv8hard ||
     "$terrain" == currentbodyv9hard || "$terrain" == currentbodyv10hard ||
     "$terrain" == currentbodyv11hard || "$terrain" == currentbodyv12hard ||
     "$terrain" == currentbodyv13hard || "$terrain" == currentbodyv14hard ||
     "$terrain" == currentbodyv15hard || "$terrain" == currentbodyv16hard ||
     "$terrain" == currentbodyv17hard || "$terrain" == currentbodyv18hard ||
     "$terrain" == currentbodyv19hard || "$terrain" == currentbodyv20train ]] ||
    { printf 'Invalid terrain: %s\n' "$terrain" >&2; exit 2; }
  [[ "$terrain" != v2robust && "$terrain" != v2goal &&
     ( "$terrain" != currentv3* || "$terrain" == currentv3core ||
       "$terrain" == currentv3reverse ) ]] ||
    [[ -n "$checkpoint" ]] ||
    { printf '%s is a continuation stage and requires a checkpoint.\n' "$terrain" >&2; exit 2; }
  [[ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null || true)" == true ]] ||
    { printf '%s is not running.\n' "$CONTAINER" >&2; exit 1; }
  [[ -x "${ROOT}/run_simple_dog.sh" ]] ||
    { printf 'Training launcher is missing: %s\n' "${ROOT}/run_simple_dog.sh" >&2; exit 1; }
  if [[ -n "$checkpoint" ]]; then
    if [[ "$terrain" == currentbodyv20train ]]; then
      [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v20_*/*.pth ]] || return 2
    else
      [[ "$checkpoint" != /workspace/projects/training/logs/rl_games/quadruped_current_body_v20_*/*.pth ]] || return 2
    fi
    [[ "$terrain" != currentbodyv4hard && "$terrain" != currentbodyv5hard &&
       "$terrain" != currentbodyv6hard && "$terrain" != currentbodyv7hard &&
       "$terrain" != currentbodyv8hard && "$terrain" != currentbodyv9hard &&
       "$terrain" != currentbodyv10hard && "$terrain" != currentbodyv11hard &&
       "$terrain" != currentbodyv12hard && "$terrain" != currentbodyv13hard &&
       "$terrain" != currentbodyv14hard && "$terrain" != currentbodyv15hard &&
       "$terrain" != currentbodyv16hard && "$terrain" != currentbodyv17hard &&
       "$terrain" != currentbodyv18hard && "$terrain" != currentbodyv19hard ]] ||
      { printf '%s must start from random actor and optimizer initialization.\n' "$terrain" >&2; exit 2; }
    [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_rough_velocity_direct/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_v2_*/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_current_v3_rough_direct/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_v3_*/*.pth ||
       "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_body_v20_*/*.pth ]] ||
      { printf 'Checkpoint is outside the simple-dog log directory: %s\n' "$checkpoint" >&2; exit 2; }
    if [[ "$terrain" == currentv3* ]]; then
      [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_current_v3_rough_direct/*.pth ||
         "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_current_v3_*/*.pth ]] ||
        { printf 'CurrentV3 terrain requires a CurrentV3 checkpoint because policy observations differ.\n' >&2; exit 2; }
    elif [[ "$terrain" == v2* ]]; then
      [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ||
         "$checkpoint" == /workspace/projects/training/logs/rl_games/quadruped_v2_*/*.pth ]] ||
        { printf 'V2 terrain requires a V2 checkpoint because policy observations differ.\n' >&2; exit 2; }
    else
      [[ "$checkpoint" != /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth &&
         "$checkpoint" != /workspace/projects/training/logs/rl_games/quadruped_v2_*/*.pth &&
         "$checkpoint" != /workspace/projects/training/logs/rl_games/simple_dog_current_v3_rough_direct/*.pth &&
         "$checkpoint" != /workspace/projects/training/logs/rl_games/quadruped_current_v3_*/*.pth ]] ||
        { printf 'A V2 or CurrentV3 checkpoint cannot be loaded into a V1 task.\n' >&2; exit 2; }
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
  if [[ "$terrain" == currentv3* || "$terrain" == currentbodyv4hard ||
        "$terrain" == currentbodyv5hard || "$terrain" == currentbodyv6hard ||
        "$terrain" == currentbodyv7hard || "$terrain" == currentbodyv8hard ||
        "$terrain" == currentbodyv9hard || "$terrain" == currentbodyv10hard ||
        "$terrain" == currentbodyv11hard || "$terrain" == currentbodyv12hard ||
        "$terrain" == currentbodyv13hard || "$terrain" == currentbodyv14hard ||
        "$terrain" == currentbodyv15hard || "$terrain" == currentbodyv16hard ||
        "$terrain" == currentbodyv17hard || "$terrain" == currentbodyv18hard ||
        "$terrain" == currentbodyv19hard || "$terrain" == currentbodyv20train ]]; then
    [[ "$simulation_fit" == /workspace/projects/training/fits/*.json ]] ||
      { printf 'Current-aware simulation fit is outside the training fits directory.\n' >&2; exit 2; }
    docker exec "$CONTAINER" test -f "$simulation_fit" ||
      { printf 'Current-aware simulation fit does not exist: %s\n' "$simulation_fit" >&2; exit 2; }
    simulation_fit_sha="$(docker exec "$CONTAINER" sha256sum "$simulation_fit" | awk '{print $1}')"
    [[ "$simulation_fit_sha" =~ ^[0-9a-f]{64}$ ]] ||
      { printf 'Could not hash the current-aware simulation fit.\n' >&2; exit 2; }
  elif [[ -n "$simulation_fit" ]]; then
    printf 'A simulation fit may be supplied only for current-aware training.\n' >&2
    exit 2
  fi
  [[ "$record_video" == 0 || "$record_video" == 1 ]] ||
    { printf 'Invalid record-video flag.\n' >&2; exit 2; }
  [[ "$video_interval" =~ ^[0-9]+$ ]] && ((video_interval >= 100 && video_interval <= 10000000)) ||
    { printf 'Invalid video interval.\n' >&2; exit 2; }
  [[ "$video_length" =~ ^[0-9]+$ ]] && ((video_length >= 50 && video_length <= 5000)) ||
    { printf 'Invalid video length.\n' >&2; exit 2; }
  [[ -z "$seed_override" || "$seed_override" =~ ^[0-9]+$ ]] ||
    { printf 'Invalid seed override.\n' >&2; exit 2; }

  if active; then
    printf 'Simple-dog training is already active.\n' >&2
    status_training
    return 3
  fi

  clear_stale_hub_lock

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
    -e "SIMPLE_DOG_SIMULATION_FIT=${simulation_fit}" \
    -e "SIMPLE_DOG_SIMULATION_FIT_SHA=${simulation_fit_sha}" \
    -e "SIMPLE_DOG_RECORD_VIDEO=${record_video}" \
    -e "SIMPLE_DOG_VIDEO_INTERVAL=${video_interval}" \
    -e "SIMPLE_DOG_VIDEO_LENGTH=${video_length}" \
    -e "SIMPLE_DOG_SEED_OVERRIDE=${seed_override}" \
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
  local container latest status progress reward gpu experiment surface task

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
  task="$(cat "$latest/task" 2>/dev/null || true)"
  if [[ -f "$latest/control_profile.json" ]]; then
    surface="$({
      sed -n 's/.*"surface"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        "$latest/control_profile.json" | head -1
    } || true)"
    if [[ "$task" == Isaac-Locomotion-CurrentBodyV4-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV5-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV6-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV7-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV8-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV9-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV10-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV11-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV12-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV13-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV14-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV15-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV16-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV17-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV18-Hard-* ||
          "$task" == Isaac-Locomotion-CurrentBodyV19-Hard-* ]]; then
      surface="Full-hard varied"
    fi
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
    start_training "${2:-512}" "${3:-500}" "${4:-}" "${5:-}" "${6:-flat}" "${7:-}" "${8:-}" "${9:-0}" "${10:-5000}" "${11:-400}" "${12:-}" "${13:-}"
    ;;
  status)
    status_training
    ;;
  latest-video)
    latest_video
    ;;
  render-latest-video)
    render_latest_video "${2:-400}" "${3:-}"
    ;;
  render-checkpoint-video)
    render_checkpoint_video "${2:-}" "${3:-400}" "${4:-v2rough}" "${5:-}" "${6:-0}" "${7:-}"
    ;;
  *)
    printf 'Usage: %s active|start [num-envs max-iterations checkpoint tuning-config terrain control-profile profile-sha record-video video-interval video-length simulation-fit seed]|status|latest-video|render-latest-video [video-length]|render-checkpoint-video checkpoint [video-length terrain control-profile sample-index simulation-fit]\n' "$0" >&2
    exit 2
    ;;
esac
