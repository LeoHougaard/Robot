#!/usr/bin/env bash
set -Eeuo pipefail

readonly WORKLOAD_LABEL="com.leo.workload=isaac-gb10"
readonly CONFIG_VERSION="7"
readonly WORKSPACE_ROOT="/home/leo/isaac-workspace"
readonly PROJECTS_DIR="${WORKSPACE_ROOT}/projects"
readonly EULA_MARKER="${WORKSPACE_ROOT}/.nvidia-eula-accepted"
readonly LAB_DOCKERFILE="${WORKSPACE_ROOT}/bin/Dockerfile.isaac-lab-gb10"
readonly ROBOT_VALIDATOR="${PROJECTS_DIR}/training/tools/validate-onshape-robot.py"

# Latest released ARM64 manifests verified from NGC on 2026-08-14.
readonly LAB_BASE_IMAGE="nvcr.io/nvidia/isaac-lab:3.0.0-beta2-post1@sha256:07a349c0aa9cadbb33b82fc9428be334084f384db21b7b0bf9f3ff6c2ee876c9"
readonly LAB_IMAGE="leo/isaac-lab-gb10:3.0.0-beta2-post1-uid1001-v2"
readonly SIM_IMAGE="nvcr.io/nvidia/isaac-sim:6.0.1@sha256:202697359628d8c06305f174d125cdcfd2e47a0815a571c1e2405253ef3e08eb"
readonly LAB_CONTAINER="isaac-lab-gb10"
readonly SIM_CONTAINER="isaac-sim-onshape"
readonly PLAYBACK_CONTAINER="isaac-lab-dog-stream"

readonly -a LAB_VOLUMES=(
  "isaac-lab3-cache-kit:/isaac-sim/kit/cache"
  "isaac-lab3-data-kit:/isaac-sim/kit/data"
  "isaac-lab3-cache-ov:/root/.cache/ov"
  "isaac-lab3-cache-pip:/root/.cache/pip"
  "isaac-lab3-cache-gl:/root/.cache/nvidia/GLCache"
  "isaac-lab3-cache-compute:/root/.nv/ComputeCache"
  "isaac-lab3-logs:/root/.nvidia-omniverse/logs"
  "isaac-lab3-carb-logs:/isaac-sim/kit/logs/Kit/Isaac-Sim"
  "isaac-lab3-data:/root/.local/share/ov/data"
  "isaac-lab3-documents:/root/Documents"
  "isaac-lab3-training-logs:/workspace/isaaclab/logs"
  "isaac-lab3-data-storage:/workspace/isaaclab/data_storage"
  "isaac-lab3-docs-build:/workspace/isaaclab/docs/_build"
)

readonly -a SIM_VOLUMES=(
  "isaac-sim6-cache-main:/isaac-sim/.cache"
  "isaac-sim6-cache-compute:/isaac-sim/.nv/ComputeCache"
  "isaac-sim6-hub-cache:/var/cache/hub"
  "isaac-sim6-logs:/isaac-sim/.nvidia-omniverse/logs"
  "isaac-sim6-config:/isaac-sim/.nvidia-omniverse/config"
  "isaac-sim6-data:/isaac-sim/.local/share/ov/data"
  "isaac-sim6-pkg:/isaac-sim/.local/share/ov/pkg"
)

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_leo() {
  [[ "$(whoami)" == "leo" ]] || die "Refusing to run as any account other than leo."
  [[ "$(uname -m)" == "aarch64" ]] || die "This configuration is pinned for the GB10 aarch64 host."
  docker info >/dev/null
}

container_exists() {
  docker container inspect "$1" >/dev/null 2>&1
}

container_running() {
  [[ "$(docker container inspect --format '{{.State.Running}}' "$1" 2>/dev/null || true)" == "true" ]]
}

image_exists() {
  docker image inspect "$1" >/dev/null 2>&1
}

ensure_eula_accepted() {
  [[ -f "$EULA_MARKER" ]] || die \
    "Run .\\Isaac-GB10.ps1 setup -AcceptNvidiaEula first. That switch records your acceptance; an agent must not accept NVIDIA's license for you."
}

assert_gpu_is_free_for() {
  local target="$1"

  if [[ "$target" != "$LAB_CONTAINER" ]] && container_running "$LAB_CONTAINER"; then
    die "${LAB_CONTAINER} is already using the GPU. Stop it first."
  fi
  if [[ "$target" != "$SIM_CONTAINER" ]] && container_running "$SIM_CONTAINER"; then
    die "${SIM_CONTAINER} is already using the GPU. Stop it first."
  fi
  if [[ "$target" != "$PLAYBACK_CONTAINER" ]] && container_running "$PLAYBACK_CONTAINER"; then
    die "${PLAYBACK_CONTAINER} is already using the GPU. Stop it first."
  fi
  if container_running "qwen36-vllm"; then
    die "qwen36-vllm is running. Stop that separately managed service explicitly before starting Isaac."
  fi
}

ensure_project_directories() {
  mkdir -p \
    "${PROJECTS_DIR}/assets/onshape" \
    "${PROJECTS_DIR}/scenes" \
    "${PROJECTS_DIR}/training" \
    "${PROJECTS_DIR}/checkpoints"

  # Isaac Lab is deliberately mapped to Leo's uid 1001. Isaac Sim must retain
  # its image uid 1234, so a narrow ACL keeps only that container user able to
  # update the project bind mount without broad host mounts or chmod 777.
  setfacl -R -x u:1000 "$PROJECTS_DIR" 2>/dev/null || true
  find "$PROJECTS_DIR" -type d -exec setfacl -x d:u:1000 {} + 2>/dev/null || true
  # A file saved from the streamed app is owned by its required UID 1234.
  # The host user cannot change that file's ACL, but it is already accessible
  # to the simulator and normally world-readable to Lab. Do not let one such
  # GUI-authored file prevent the other container from starting.
  setfacl -R -m u:1234:rwx "$PROJECTS_DIR" 2>/dev/null || true
  setfacl -d -m u:1234:rwx "$PROJECTS_DIR"
}

ensure_volume() {
  local name="$1"
  local role="$2"
  if ! docker volume inspect "$name" >/dev/null 2>&1; then
    docker volume create \
      --label "$WORKLOAD_LABEL" \
      --label "com.leo.role=${role}" \
      "$name" >/dev/null
  fi
}

ensure_all_volumes() {
  local spec
  for spec in "${LAB_VOLUMES[@]}"; do
    ensure_volume "${spec%%:*}" "isaac-lab"
  done
  for spec in "${SIM_VOLUMES[@]}"; do
    ensure_volume "${spec%%:*}" "isaac-sim"
  done
}

add_volume_args() {
  local array_name="$1"
  local -n specs="$array_name"
  local spec
  VOLUME_ARGS=()
  for spec in "${specs[@]}"; do
    VOLUME_ARGS+=(--volume "$spec")
  done
}

initialize_volume_ownership() {
  local array_name="$1"
  local image="$2"
  local owner="$3"
  local -n specs="$array_name"
  local spec

  # NVIDIA's non-root images need their cache/data volumes owned by the
  # runtime uid. Each one-shot root process can reach only one named volume,
  # has no GPU, and has no network access.
  for spec in "${specs[@]}"; do
    docker container run --rm \
      --network none \
      --user 0:0 \
      --env ACCEPT_EULA=Y \
      --volume "${spec%%:*}:/target" \
      --entrypoint /bin/chown \
      "$image" \
      -R "$owner" /target
  done
}

remove_stopped_container_if_recreate_needed() {
  local name="$1"
  local image="$2"
  local expected_id actual_id actual_config

  container_exists "$name" || return 0
  container_running "$name" && return 0

  expected_id="$(docker image inspect --format '{{.Id}}' "$image")"
  actual_id="$(docker container inspect --format '{{.Image}}' "$name")"
  actual_config="$(docker container inspect \
    --format '{{index .Config.Labels "com.leo.config-version"}}' "$name")"
  if [[ "$actual_id" != "$expected_id" || "$actual_config" != "$CONFIG_VERSION" ]]; then
    docker container rm "$name" >/dev/null
  fi
}

create_lab_container() {
  image_exists "$LAB_IMAGE" || die "Isaac Lab image is absent. Run setup first."
  ensure_project_directories
  ensure_all_volumes
  remove_stopped_container_if_recreate_needed "$LAB_CONTAINER" "$LAB_IMAGE"
  container_exists "$LAB_CONTAINER" && return 0

  add_volume_args LAB_VOLUMES
  docker container create \
    --name "$LAB_CONTAINER" \
    --label "$WORKLOAD_LABEL" \
    --label "com.leo.role=training" \
    --label "com.leo.config-version=${CONFIG_VERSION}" \
    --gpus all \
    --network bridge \
    --restart no \
    --shm-size 16g \
    --user "$(id -u):1000" \
    --env ACCEPT_EULA=Y \
    --env USER=leo \
    --env LOGNAME=leo \
    --env MPLCONFIGDIR=/root/.cache/matplotlib \
    --volume "${PROJECTS_DIR}:/workspace/projects:rw" \
    "${VOLUME_ARGS[@]}" \
    --workdir /workspace/isaaclab \
    --entrypoint /bin/bash \
    "$LAB_IMAGE" \
    -lc 'trap "exit 0" TERM INT; while :; do sleep 3600 & wait $!; done' >/dev/null
  initialize_volume_ownership LAB_VOLUMES "$LAB_IMAGE" "$(id -u):1000"
}

start_lab() {
  ensure_eula_accepted
  assert_gpu_is_free_for "$LAB_CONTAINER"
  create_lab_container
  if ! container_running "$LAB_CONTAINER"; then
    docker container start "$LAB_CONTAINER" >/dev/null
  fi
  printf 'Isaac Lab is running headlessly in %s.\n' "$LAB_CONTAINER"
  printf 'Projects: %s (container: /workspace/projects)\n' "$PROJECTS_DIR"
  inspect_container "$LAB_CONTAINER"
}

stop_container() {
  local name="$1"
  if container_running "$name"; then
    docker container stop --time 30 "$name" >/dev/null
    printf 'Stopped %s. GPU/RAM are released; images, caches, and projects remain.\n' "$name"
  elif container_exists "$name"; then
    printf '%s is already stopped.\n' "$name"
  else
    printf '%s has not been created.\n' "$name"
  fi
}

inspect_container() {
  local name="$1"
  docker container inspect "$name" --format \
    'name={{.Name}} running={{.State.Running}} privileged={{.HostConfig.Privileged}} network={{.HostConfig.NetworkMode}} restart={{.HostConfig.RestartPolicy.Name}} image={{.Config.Image}}'
}

test_lab() {
  start_lab
  printf 'Running a one-iteration PhysX/RSL-RL Cartpole smoke test...\n'
  docker container exec \
    --workdir /workspace/isaaclab \
    "$LAB_CONTAINER" \
    ./isaaclab.sh train \
      --rl_library rsl_rl \
      --task=Isaac-Cartpole-Direct-v0 \
      --num_envs=16 \
      --max_iterations=1 \
      physics=physx
}

test_newton() {
  start_lab
  printf 'Running a one-iteration Newton/MuJoCo-Warp RSL-RL Cartpole smoke test...\n'
  docker container exec \
    --workdir /workspace/isaaclab \
    "$LAB_CONTAINER" \
    ./isaaclab.sh train \
      --rl_library rsl_rl \
      --task=Isaac-Cartpole-Direct-v0 \
      --num_envs=16 \
      --max_iterations=1 \
      physics=newton_mjwarp
}

test_robot() {
  local robot_name="${1:-}"
  [[ "$robot_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || die \
    "Robot name must be 1-64 characters using only letters, numbers, dot, underscore, or hyphen."
  local asset="${PROJECTS_DIR}/assets/onshape/${robot_name}/robot.usda"
  [[ -f "$asset" ]] || die "Onshape robot asset is missing: ${asset}"
  [[ -f "$ROBOT_VALIDATOR" ]] || die "Onshape robot validator is missing."

  start_lab
  docker container exec \
    --workdir /workspace/isaaclab \
    "$LAB_CONTAINER" \
    ./isaaclab.sh -p \
      /workspace/projects/training/tools/validate-onshape-robot.py \
      --asset "/workspace/projects/assets/onshape/${robot_name}/robot.usda"
}

shell_lab() {
  start_lab >/dev/null
  exec docker container exec \
    --interactive \
    --tty \
    --workdir /workspace/projects \
    "$LAB_CONTAINER" \
    /bin/bash
}

private_lan_ip() {
  local ip
  ip="$(ip -4 route get 1.1.1.1 | awk '{for (i=1; i<=NF; i++) if ($i=="src") {print $(i+1); exit}}')"
  [[ -n "$ip" ]] || die "Could not determine the GB10 private IPv4 address."
  printf '%s\n' "$ip"
}

ports_available_for_streaming() {
  if ss -H -lnt 'sport = :49100' | grep -q .; then
    die "TCP port 49100 is already in use."
  fi
  if ss -H -lnu 'sport = :47998' | grep -q .; then
    die "UDP port 47998 is already in use."
  fi
}

create_sim_container() {
  local host_ip="$1"
  image_exists "$SIM_IMAGE" || die "Isaac Sim image is absent. Run setup first."
  ensure_project_directories
  ensure_all_volumes
  remove_stopped_container_if_recreate_needed "$SIM_CONTAINER" "$SIM_IMAGE"
  container_exists "$SIM_CONTAINER" && return 0

  add_volume_args SIM_VOLUMES
  docker container create \
    --name "$SIM_CONTAINER" \
    --label "$WORKLOAD_LABEL" \
    --label "com.leo.role=onshape-import" \
    --label "com.leo.config-version=${CONFIG_VERSION}" \
    --gpus all \
    --network host \
    --restart no \
    --user 1234:1234 \
    --env ACCEPT_EULA=Y \
    --env "ISAACSIM_HOST=${host_ip}" \
    --env ISAACSIM_SIGNAL_PORT=49100 \
    --env ISAACSIM_STREAM_PORT=47998 \
    --volume "${PROJECTS_DIR}:/workspace/projects:rw" \
    "${VOLUME_ARGS[@]}" \
    --workdir /isaac-sim \
    --entrypoint /bin/bash \
    "$SIM_IMAGE" \
    -lc 'exec ./runheadless.sh -v' >/dev/null
  initialize_volume_ownership SIM_VOLUMES "$SIM_IMAGE" "1234:1234"
}

start_onshape() {
  [[ "${1:-}" == "--allow-private-lan-streaming" ]] || die \
    "Onshape import needs Isaac Sim streaming. Re-run with -AllowPrivateLanStreaming after accepting that ports 49100/TCP and 47998/UDP have no authentication or encryption."
  ensure_eula_accepted
  assert_gpu_is_free_for "$SIM_CONTAINER"
  local host_ip
  host_ip="$(private_lan_ip)"

  if ! container_running "$SIM_CONTAINER"; then
    ports_available_for_streaming
  fi
  create_sim_container "$host_ip"
  if ! container_running "$SIM_CONTAINER"; then
    docker container start "$SIM_CONTAINER" >/dev/null
  fi

  printf 'Isaac Sim streaming is starting at %s.\n' "$host_ip"
  printf 'Connect with Isaac Sim WebRTC Streaming Client 2.0.0 using server %s.\n' "$host_ip"
  printf 'Wait for "Isaac Sim Full Streaming App is loaded" in the logs.\n'
  printf 'Onshape assets: /workspace/projects/assets/onshape\n'
  printf 'Layered Isaac scenes: /workspace/projects/scenes\n'
  inspect_container "$SIM_CONTAINER"
}

create_playback_container() {
  local host_ip="$1"
  local checkpoint="$2"
  local terrain="${3:-flat}"
  local task="Isaac-Velocity-Flat-Simple-Dog-Direct-Play-v0"
  local playback_envs=1
  [[ "$terrain" == "flat" || "$terrain" == "rough" || "$terrain" == "v2core" || "$terrain" == "v2rough" ]] ||
    die "Playback terrain must be flat, rough, v2core, or v2rough."
  if [[ "$terrain" == "rough" ]]; then
    task="Isaac-Velocity-Rough-Simple-Dog-Direct-Play-v0"
    playback_envs=4
  elif [[ "$terrain" == "v2core" ]]; then
    task="Isaac-Locomotion-V2-Simple-Dog-Direct-Play-v0"
  elif [[ "$terrain" == "v2rough" ]]; then
    task="Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-Play-v0"
  fi
  image_exists "$LAB_IMAGE" || die "Isaac Lab image is absent. Run setup first."
  ensure_project_directories
  ensure_all_volumes

  if container_exists "$PLAYBACK_CONTAINER"; then
    container_running "$PLAYBACK_CONTAINER" && return 0
    docker container rm "$PLAYBACK_CONTAINER" >/dev/null
  fi

  add_volume_args LAB_VOLUMES
  docker container create \
    --name "$PLAYBACK_CONTAINER" \
    --label "$WORKLOAD_LABEL" \
    --label "com.leo.role=dog-policy-stream" \
    --label "com.leo.config-version=${CONFIG_VERSION}" \
    --gpus all \
    --network host \
    --restart no \
    --shm-size 16g \
    --user "$(id -u):1000" \
    --env ACCEPT_EULA=Y \
    --env USER=leo \
    --env LOGNAME=leo \
    --env MPLCONFIGDIR=/root/.cache/matplotlib \
    --env "ISAACSIM_HOST=${host_ip}" \
    --env ISAACSIM_SIGNAL_PORT=49100 \
    --env ISAACSIM_STREAM_PORT=47998 \
    --volume "${PROJECTS_DIR}:/workspace/projects:rw" \
    "${VOLUME_ARGS[@]}" \
    --workdir /workspace/projects/training \
    --entrypoint /bin/bash \
    "$LAB_IMAGE" \
    -lc "exec env PYTHONPATH=/workspace/projects/training PYTHONUNBUFFERED=1 /workspace/isaaclab/isaaclab.sh -p /workspace/projects/training/play_simple_dog.py --task=${task} --checkpoint=${checkpoint} --num_envs=${playback_envs} --livestream=2 --real-time --viz=kit" >/dev/null
  initialize_volume_ownership LAB_VOLUMES "$LAB_IMAGE" "$(id -u):1000"
}

start_dog_playback() {
  [[ "${1:-}" == "--allow-private-lan-streaming" ]] || die \
    "Dog playback needs Isaac Sim streaming. Re-run with -AllowPrivateLanStreaming after accepting that ports 49100/TCP and 47998/UDP have no authentication or encryption."
  local checkpoint="${2:-}"
  local terrain="${3:-flat}"
  [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_rough_velocity_direct/*.pth ||
     "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ]] || die \
    "Playback checkpoint must be a .pth file below the simple-dog log directory."
  if [[ "$terrain" == "v2core" || "$terrain" == "v2rough" ]]; then
    [[ "$checkpoint" == /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ]] || die \
      "V2 playback requires a V2 checkpoint."
  else
    [[ "$checkpoint" != /workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/*.pth ]] || die \
      "A V2 checkpoint requires a V2 playback task."
  fi
  [[ -f "${PROJECTS_DIR}${checkpoint#/workspace/projects}" ]] || die \
    "Playback checkpoint does not exist: ${checkpoint}"
  [[ -f "${PROJECTS_DIR}/training/play_simple_dog.py" ]] || die \
    "Dog playback task is not deployed. Run Start-SimpleDogPlayback.ps1 from Windows."

  ensure_eula_accepted
  assert_gpu_is_free_for "$PLAYBACK_CONTAINER"
  local host_ip
  host_ip="$(private_lan_ip)"
  if ! container_running "$PLAYBACK_CONTAINER"; then
    ports_available_for_streaming
  fi
  create_playback_container "$host_ip" "$checkpoint" "$terrain"
  if ! container_running "$PLAYBACK_CONTAINER"; then
    docker container start "$PLAYBACK_CONTAINER" >/dev/null
  fi

  printf 'Simple Dog policy streaming is starting at %s.\n' "$host_ip"
  printf 'Connect the Isaac Sim WebRTC client to %s.\n' "$host_ip"
  printf 'Checkpoint: %s\n' "$checkpoint"
  inspect_container "$PLAYBACK_CONTAINER"
}

logs_dog_playback() {
  container_exists "$PLAYBACK_CONTAINER" || die "${PLAYBACK_CONTAINER} has not been created."
  docker container logs --tail 160 "$PLAYBACK_CONTAINER"
}

install_export() {
  local robot_name="${1:-}"
  local zip_path="${2:-}"
  [[ "$robot_name" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || die \
    "Robot name must be 1-64 characters using only letters, numbers, dot, underscore, or hyphen."
  [[ "$zip_path" == "${WORKSPACE_ROOT}/incoming/"*.zip ]] || die \
    "Export archive must be staged below ${WORKSPACE_ROOT}/incoming."
  [[ -f "$zip_path" ]] || die "Staged export archive does not exist."

  ensure_project_directories
  local timestamp staging_dir target_dir backup_dir
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  staging_dir="${WORKSPACE_ROOT}/incoming/${robot_name}.${timestamp}.$$"
  target_dir="${PROJECTS_DIR}/assets/onshape/${robot_name}"
  backup_dir="${PROJECTS_DIR}/assets/onshape/.backups/${robot_name}.${timestamp}"
  mkdir -p "$staging_dir"
  unzip -q "$zip_path" -d "$staging_dir"
  [[ -f "${staging_dir}/robot.usda" ]] || die \
    "The archive is not an Omniverse Publisher export: robot.usda is missing at its root."
  if find "$staging_dir" -type l -print -quit | grep -q .; then
    die "Refusing an export archive that creates symbolic links."
  fi

  if [[ -e "$target_dir" ]]; then
    mkdir -p "$(dirname "$backup_dir")"
    mv "$target_dir" "$backup_dir"
    printf 'Previous export moved to recoverable backup %s.\n' "$backup_dir"
  fi
  mv "$staging_dir" "$target_dir"
  rm -f -- "$zip_path"
  setfacl -R -x u:1000 "$target_dir" 2>/dev/null || true
  setfacl -R -m u:1234:rwx "$target_dir"
  find "$target_dir" -type d -exec setfacl -d -m u:1234:rwx {} +
  printf 'Installed Onshape Omniverse Publisher export at %s.\n' "$target_dir"
  printf 'Container asset path: /workspace/projects/assets/onshape/%s/robot.usda\n' "$robot_name"
  printf 'Keep authored Isaac scenes separate under /workspace/projects/scenes.\n'
}

logs_onshape() {
  container_exists "$SIM_CONTAINER" || die "${SIM_CONTAINER} has not been created."
  docker container logs --tail 120 "$SIM_CONTAINER"
}

status() {
  printf 'Host: %s user=%s arch=%s\n' "$(hostname)" "$(whoami)" "$(uname -m)"
  printf 'Driver/GPU:\n'
  nvidia-smi --query-gpu=name,driver_version,memory.used --format=csv,noheader || true
  printf 'Isaac containers:\n'
  local name
  for name in "$LAB_CONTAINER" "$SIM_CONTAINER" "$PLAYBACK_CONTAINER"; do
    if container_exists "$name"; then
      inspect_container "$name"
    else
      printf 'name=/%s state=absent\n' "$name"
    fi
  done
  printf 'Other known GPU workload:\n'
  if container_exists "qwen36-vllm"; then
    docker container inspect "qwen36-vllm" --format 'name={{.Name}} running={{.State.Running}} image={{.Config.Image}}'
  else
    printf 'name=/qwen36-vllm state=absent\n'
  fi
  printf 'Pinned images:\n'
  for name in "$LAB_BASE_IMAGE" "$LAB_IMAGE" "$SIM_IMAGE"; do
    if image_exists "$name"; then
      docker image inspect "$name" --format 'present architecture={{.Architecture}} id={{.Id}} repoDigests={{json .RepoDigests}}'
    else
      printf 'absent %s\n' "$name"
    fi
  done
  printf 'Project directory: %s\n' "$PROJECTS_DIR"
}

space_report() {
  df -h / /var/lib/docker
  docker system df
  if [[ -d "$WORKSPACE_ROOT" ]]; then
    du -sh "$WORKSPACE_ROOT"
  fi
  printf 'Isaac-owned volumes:\n'
  docker volume ls --filter "label=${WORKLOAD_LABEL}" --format '  {{.Name}}'
}

setup() {
  [[ "${1:-}" == "--accept-nvidia-eula" ]] || die \
    "Setup requires explicit NVIDIA license acceptance from Leo: use -AcceptNvidiaEula."

  local available_bytes
  available_bytes="$(df --output=avail -B1 /var/lib/docker | tail -n 1 | tr -d ' ')"
  (( available_bytes >= 107374182400 )) || die \
    "At least 100 GiB free under Docker storage is required for both pinned images and working caches."

  ensure_project_directories
  ensure_all_volumes
  : >"$EULA_MARKER"
  chmod 0600 "$EULA_MARKER"

  printf 'Pulling pinned Isaac Lab ARM64 base image...\n'
  docker image pull "$LAB_BASE_IMAGE"
  printf 'Pulling pinned Isaac Sim ARM64 image...\n'
  docker image pull "$SIM_IMAGE"

  local image architecture
  for image in "$LAB_BASE_IMAGE" "$SIM_IMAGE"; do
    architecture="$(docker image inspect --format '{{.Architecture}}' "$image")"
    [[ "$architecture" == "arm64" ]] || die "Unexpected image architecture for ${image}: ${architecture}"
  done

  [[ -f "$LAB_DOCKERFILE" ]] || die "Isaac Lab runtime Dockerfile is missing."
  printf 'Building the minimal non-root Leo runtime layer...\n'
  docker image build \
    --network none \
    --tag "$LAB_IMAGE" \
    --file "$LAB_DOCKERFILE" \
    "${WORKSPACE_ROOT}/bin"
  architecture="$(docker image inspect --format '{{.Architecture}}' "$LAB_IMAGE")"
  [[ "$architecture" == "arm64" ]] || die \
    "Unexpected image architecture for ${LAB_IMAGE}: ${architecture}"

  printf 'Validating GPU visibility in the Isaac Lab image...\n'
  docker container run --rm \
    --gpus all \
    --network bridge \
    --env ACCEPT_EULA=Y \
    --entrypoint /usr/bin/nvidia-smi \
    "$LAB_IMAGE"

  printf 'Setup complete. Run test-lab and test-newton for functional backend tests.\n'
  status
}

stop_all() {
  stop_container "$PLAYBACK_CONTAINER"
  stop_container "$SIM_CONTAINER"
  stop_container "$LAB_CONTAINER"
}

cleanup() {
  local remove_images=false
  local remove_caches=false
  shift
  while (($#)); do
    case "$1" in
      --images) remove_images=true ;;
      --caches) remove_caches=true ;;
      *) die "Unknown cleanup option: $1" ;;
    esac
    shift
  done
  [[ "$remove_images" == true || "$remove_caches" == true ]] || die \
    "Choose -RemoveImages and/or -RemoveCaches."

  stop_all
  local name
  for name in "$LAB_CONTAINER" "$SIM_CONTAINER" "$PLAYBACK_CONTAINER"; do
    if container_exists "$name"; then
      docker container rm "$name" >/dev/null
      printf 'Removed reproducible container %s.\n' "$name"
    fi
  done

  if [[ "$remove_caches" == true ]]; then
    local spec volume_label
    for spec in "${LAB_VOLUMES[@]}" "${SIM_VOLUMES[@]}"; do
      name="${spec%%:*}"
      if docker volume inspect "$name" >/dev/null 2>&1; then
        volume_label="$(docker volume inspect --format '{{index .Labels "com.leo.workload"}}' "$name")"
        [[ "$volume_label" == "isaac-gb10" ]] || die "Refusing to remove unlabeled volume ${name}."
        docker volume rm "$name" >/dev/null
        printf 'Removed cache/config volume %s.\n' "$name"
      fi
    done
  fi

  if [[ "$remove_images" == true ]]; then
    for name in "$LAB_IMAGE" "$LAB_BASE_IMAGE" "$SIM_IMAGE"; do
      if image_exists "$name"; then
        docker image rm "$name"
      fi
    done
  fi

  printf 'Preserved project data at %s.\n' "$PROJECTS_DIR"
}

usage() {
  cat <<'EOF'
Usage: isaac-gb10.sh COMMAND

  setup --accept-nvidia-eula
  start-lab | stop-lab | shell | test-lab | test-newton
  test-robot ROBOT_NAME
  start-onshape --allow-private-lan-streaming | stop-onshape | logs-onshape
  start-dog-playback --allow-private-lan-streaming CHECKPOINT | stop-dog-playback | logs-dog-playback
  install-export ROBOT_NAME /home/leo/isaac-workspace/incoming/ARCHIVE.zip
  stop-all | status | space
  cleanup --confirmed [--images] [--caches]
EOF
}

main() {
  require_leo
  local command="${1:-}"
  shift || true
  case "$command" in
    setup) setup "$@" ;;
    start-lab) start_lab ;;
    stop-lab) stop_container "$LAB_CONTAINER" ;;
    shell) shell_lab ;;
    test-lab) test_lab ;;
    test-newton) test_newton ;;
    test-robot) test_robot "$@" ;;
    start-onshape) start_onshape "$@" ;;
    stop-onshape) stop_container "$SIM_CONTAINER" ;;
    logs-onshape) logs_onshape ;;
    start-dog-playback) start_dog_playback "$@" ;;
    stop-dog-playback) stop_container "$PLAYBACK_CONTAINER" ;;
    logs-dog-playback) logs_dog_playback ;;
    install-export) install_export "$@" ;;
    stop-all) stop_all ;;
    status) status ;;
    space) space_report ;;
    cleanup)
      [[ "${1:-}" == "--confirmed" ]] || die "Cleanup requires -ConfirmCleanup."
      cleanup "$@"
      ;;
    help|-h|--help|"") usage ;;
    *) die "Unknown command: ${command}" ;;
  esac
}

main "$@"
