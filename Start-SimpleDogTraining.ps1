[CmdletBinding()]
param(
    [ValidateRange(128, 16384)]
    [int]$NumEnvs = 512,

    [ValidateRange(1, 100000)]
    [int]$MaxIterations = 500,

    [ValidateRange(0, 2147483647)]
    [Nullable[int]]$Seed = $null,

    [ValidateSet("Flat", "Rough", "V2Core", "V2Robust", "V2Goal", "V2Rough", "CurrentV3Core", "CurrentV3Reverse", "CurrentV3ForwardSpecialist", "CurrentV3ReverseSpecialist", "CurrentV3Strafe", "CurrentV3Turn", "CurrentV3Goal", "CurrentV3Posture", "CurrentV3Rough", "CurrentBodyV4Hard", "CurrentBodyV5Hard", "CurrentBodyV6Hard", "CurrentBodyV7Hard", "CurrentBodyV8Hard", "CurrentBodyV9Hard", "CurrentBodyV10Hard")]
    [string]$Terrain = "Flat",

    [string]$Checkpoint = "",

    [string]$TuningConfig = "",

    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$ControlProfile = "",

    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$SimulationFit = "",

    # Internal recovery path for reward-only continuations after this exact
    # robot/profile articulation has already passed the Isaac preflight.
    [switch]$ReuseValidatedRobot
)

$ErrorActionPreference = "Stop"

$sshHost = if ($env:ROBOT_GB10_HOST) { $env:ROBOT_GB10_HOST.Trim() } else { $null }
if ($sshHost -and $sshHost -notmatch '^[A-Za-z0-9.-]+$') {
    throw "ROBOT_GB10_HOST must be a hostname or IPv4 address."
}
if (-not $sshHost) {
    foreach ($attempt in 1..3) {
        try {
            $sshHost = [System.Net.Dns]::GetHostAddresses("gx10-ddb2.local") |
                Where-Object AddressFamily -eq InterNetwork |
                Select-Object -First 1 -ExpandProperty IPAddressToString
        }
        catch {
            $sshHost = $null
        }
        if ($sshHost) { break }
        Start-Sleep -Seconds 1
    }
}
if (-not $sshHost) {
    throw "Could not resolve gx10-ddb2.local to an IPv4 address."
}
$sshTarget = "leo@$sshHost"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$localTraining = Join-Path $PSScriptRoot "training"
$remoteTraining = "/home/leo/isaac-workspace/projects/training"
$profileHash = ""
$remoteControlProfile = ""
$remoteSimulationFit = ""
$simulationFitHash = ""
$recordVideo = "0"
$videoInterval = 5000
$videoLength = 400
$sshOptions = @(
    "-i", $keyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "HostKeyAlias=gx10-ddb2.local"
)

if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    throw "NVIDIA Sync SSH key was not found."
}
if (-not (Test-Path -LiteralPath $localTraining -PathType Container)) {
    throw "Local simple-dog training package was not found: $localTraining"
}
if ($Checkpoint -and $Checkpoint -notmatch '^/workspace/projects/training/logs/rl_games/(simple_dog_(rough_)?velocity_direct|simple_dog_v2_locomotion_direct|quadruped_v2_[A-Za-z0-9_-]+|simple_dog_current_v3_rough_direct|quadruped_current_v3_[A-Za-z0-9_-]+)/[A-Za-z0-9_./-]+\.pth$') {
    throw "Checkpoint must be a .pth file below a supported simple-dog training log directory."
}
$isV2Terrain = $Terrain -in @("V2Core", "V2Robust", "V2Goal", "V2Rough")
$isV2Checkpoint = $Checkpoint -match '^/workspace/projects/training/logs/rl_games/(simple_dog_v2_locomotion_direct|quadruped_v2_[A-Za-z0-9_-]+)/'
$currentTerrains = @("CurrentV3Core", "CurrentV3Reverse", "CurrentV3ForwardSpecialist", "CurrentV3ReverseSpecialist", "CurrentV3Strafe", "CurrentV3Turn", "CurrentV3Goal", "CurrentV3Posture", "CurrentV3Rough")
$isCurrentTerrain = $Terrain -in $currentTerrains
$isCurrentCheckpoint = $Checkpoint -match '^/workspace/projects/training/logs/rl_games/(simple_dog_current_v3_rough_direct|quadruped_current_v3_[A-Za-z0-9_-]+)/'
$v4Terrains = @("CurrentBodyV4Hard")
$isV4Terrain = $Terrain -in $v4Terrains
$v5Terrains = @("CurrentBodyV5Hard")
$isV5Terrain = $Terrain -in $v5Terrains
$v6Terrains = @("CurrentBodyV6Hard")
$isV6Terrain = $Terrain -in $v6Terrains
$v7Terrains = @("CurrentBodyV7Hard")
$isV7Terrain = $Terrain -in $v7Terrains
$v8Terrains = @("CurrentBodyV8Hard")
$isV8Terrain = $Terrain -in $v8Terrains
$v9Terrains = @("CurrentBodyV9Hard")
$isV9Terrain = $Terrain -in $v9Terrains
$v10Terrains = @("CurrentBodyV10Hard")
$isV10Terrain = $Terrain -in $v10Terrains
$currentBodyTerrains = $v4Terrains + $v5Terrains + $v6Terrains + $v7Terrains + $v8Terrains + $v9Terrains + $v10Terrains
if (($isV4Terrain -or $isV5Terrain -or $isV6Terrain -or $isV7Terrain -or $isV8Terrain -or $isV9Terrain -or $isV10Terrain) -and $Checkpoint) {
    throw "$Terrain requires a random actor and optimizer start; checkpoints are forbidden."
}
if ($Checkpoint -and (($isV2Terrain -and -not $isV2Checkpoint) -or ($isCurrentTerrain -and -not $isCurrentCheckpoint) -or (-not $isV2Terrain -and -not $isCurrentTerrain -and $Terrain -notin $currentBodyTerrains -and ($isV2Checkpoint -or $isCurrentCheckpoint)))) {
    throw "V1, V2, CurrentV3, and CurrentBody checkpoints are not interchangeable because their policy observations differ."
}
if ($Terrain -in @("V2Robust", "V2Goal") -and -not $Checkpoint) {
    throw "$Terrain is a continuation stage and requires a passing V2 checkpoint."
}
if ($isCurrentTerrain -and $Terrain -notin @("CurrentV3Core", "CurrentV3Reverse") -and -not $Checkpoint) {
    throw "$Terrain is a continuation stage and requires a CurrentV3 checkpoint."
}
if ($TuningConfig -and $TuningConfig -notmatch '^/workspace/projects/autoresearch/[A-Za-z0-9_./-]+\.json$') {
    throw "TuningConfig must be a JSON file below /workspace/projects/autoresearch."
}
$requestedTerrain = $Terrain
$requestedNumEnvs = $NumEnvs
$requestedMaxIterations = $MaxIterations
$requestedCheckpoint = $Checkpoint
if ($ControlProfile) {
    $resolvedControlProfile = (Resolve-Path -LiteralPath $ControlProfile).Path
    Push-Location $PSScriptRoot
    try {
        $validationText = & python -m control_center.validate_profile $resolvedControlProfile --launch
    }
    finally {
        Pop-Location
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Control profile validation failed: $validationText"
    }
    $validated = $validationText | ConvertFrom-Json
    if ($requestedTerrain -in ($currentTerrains + $currentBodyTerrains)) {
        $Terrain = $requestedTerrain
        $NumEnvs = $requestedNumEnvs
        $MaxIterations = $requestedMaxIterations
        $Checkpoint = $requestedCheckpoint
    }
    else {
        $Terrain = $validated.stage
        $NumEnvs = $validated.num_envs
        $MaxIterations = $validated.max_iterations
        $Checkpoint = $validated.checkpoint
    }
    $profileHash = $validated.hash
    $recordVideo = if ($validated.record_video) { "1" } else { "0" }
    $videoInterval = $validated.video_interval
    $videoLength = $validated.video_length
    $remoteControlProfile = "/workspace/projects/training/control_profiles/$($validated.profile_id)-$($profileHash.Substring(0, 12)).json"
}
if ($Terrain -in ($currentTerrains + $currentBodyTerrains)) {
    if (-not $SimulationFit) {
        throw "$Terrain requires the provenance-bearing simulation-fit.json."
    }
    $resolvedSimulationFit = (Resolve-Path -LiteralPath $SimulationFit).Path
    $fit = Get-Content -Raw -LiteralPath $resolvedSimulationFit | ConvertFrom-Json
    if ($fit.report_schema_version -ne 2 -or $fit.runs.Count -ne 1 -or $fit.runs[0].frame_count -ne 2189) {
        throw "$Terrain requires the schema-2 fit for the 2,189-frame capture."
    }
    if ($fit.runs[0].data_quality.current_complete_frames -ne 2189 -or $fit.runs[0].data_quality.incomplete_feedback_frames -ne 0) {
        throw "$Terrain fit does not contain complete current and critical feedback."
    }
    $simulationFitHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedSimulationFit).Hash.ToLowerInvariant()
    $fitFamily = if ($isV5Terrain -or $isV6Terrain -or $isV7Terrain -or $isV8Terrain -or $isV9Terrain -or $isV10Terrain) {
        "current-body-v5"
    }
    elseif ($isV4Terrain) {
        "current-body-v4"
    }
    else {
        "current-v3"
    }
    $remoteSimulationFit = "/workspace/projects/training/fits/$fitFamily-$($simulationFitHash.Substring(0, 12)).json"
}
elseif ($SimulationFit) {
    throw "SimulationFit is accepted only for CurrentV3 and CurrentBody stages."
}

$identity = (& ssh @sshOptions $sshTarget "whoami").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}

$activeOutput = & ssh @sshOptions $sshTarget `
    "docker exec isaac-lab-gb10 pgrep -f '[t]rain_simple_dog.py' >/dev/null 2>&1 && printf active || true"
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the current Isaac Lab workload."
}
$active = ($activeOutput -join "").Trim()
if ($active) {
    Write-Host "Simple-dog training is already running; it was not restarted."
    & (Join-Path $PSScriptRoot "Get-SimpleDogTrainingStatus.ps1")
    exit $LASTEXITCODE
}

# This starts only the reusable, unprivileged Isaac Lab container. It refuses
# to compete with the streamed Isaac Sim workload.
& (Join-Path $PSScriptRoot "Start-IsaacLab.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Could not start the Isaac Lab container."
}

# A completed Isaac run may leave a zero-byte per-user Hub lock even though
# no Hub process remains. SimulationApp then stalls before scene creation.
# Remove only those exact transient locks, and only after proving that no Hub
# process is alive in the unprivileged training container.
$clearStaleHubLock = @'
docker exec --user 0 isaac-lab-gb10 sh -c '
  if ! pgrep -x omni.hub >/dev/null 2>&1 &&
     ! pgrep -x omni-hub >/dev/null 2>&1 &&
     ! pgrep -x hub >/dev/null 2>&1 &&
     ! pgrep -x hub-daemon >/dev/null 2>&1; then
    rm -f -- /tmp/hub-leo.lock /tmp/hub-isaac-sim.lock
  fi
'
'@
& ssh @sshOptions $sshTarget $clearStaleHubLock
if ($LASTEXITCODE -ne 0) {
    throw "Could not check the stale Isaac Hub lock before training."
}

$remoteDirectories = @(
    $remoteTraining,
    "$remoteTraining/assets",
    "$remoteTraining/diagnostics",
    "$remoteTraining/simple_dog_task",
    "$remoteTraining/simple_dog_task/agents",
    "$remoteTraining/simple_dog_task_v2",
    "$remoteTraining/simple_dog_task_v2/agents",
    "$remoteTraining/simple_dog_task_current",
    "$remoteTraining/simple_dog_task_current/agents",
    "$remoteTraining/simple_dog_task_current_body_v4",
    "$remoteTraining/simple_dog_task_current_body_v4/agents",
    "$remoteTraining/simple_dog_task_current_body_v5",
    "$remoteTraining/simple_dog_task_current_body_v5/agents",
    "$remoteTraining/simple_dog_task_current_body_v6",
    "$remoteTraining/simple_dog_task_current_body_v6/agents",
    "$remoteTraining/simple_dog_task_current_body_v7",
    "$remoteTraining/simple_dog_task_current_body_v7/agents",
    "$remoteTraining/simple_dog_task_current_body_v8",
    "$remoteTraining/simple_dog_task_current_body_v8/agents",
    "$remoteTraining/simple_dog_task_current_body_v9",
    "$remoteTraining/simple_dog_task_current_body_v9/agents",
    "$remoteTraining/simple_dog_task_current_body_v10",
    "$remoteTraining/simple_dog_task_current_body_v10/agents",
    "$remoteTraining/control_profiles",
    "$remoteTraining/fits"
)
& ssh @sshOptions $sshTarget ("install -d -m 0755 " + ($remoteDirectories -join " "))
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare the remote training directories."
}

$copies = @(
    @{ Local = Join-Path $localTraining "train_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "play_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "pose_goal_controller.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "evaluate_simple_dog_policy.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "evaluate_simple_dog_policy.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "export_v2_policy.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "inspect_policy_checkpoint.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "inspect_simple_dog_run.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "prepare_rough_continuation_checkpoint.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "simple_dog_tuning.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "current_policy_fit.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "terrain_curriculum.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "video_camera.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "v4_difficulty_ramp.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "robot_control_profile.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "validate_control_profile_robot.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "validate_control_profile_robot.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "run_simple_dog.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "simple-dog-gb10.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "run_simple_dog_scratch_seed_queue.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "ensure_simple_dog_meshes.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "prepare_control_profile_asset.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "convert_onshape_gltf_to_usd.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "validate_simple_dog_stability.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "assets\simple_dog_training.usda"; Remote = "$remoteTraining/assets" },
    @{ Local = Join-Path $localTraining "simple_dog_task\__init__.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = Join-Path $localTraining "simple_dog_task\simple_dog_env.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = Join-Path $localTraining "simple_dog_task\simple_dog_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = Join-Path $localTraining "simple_dog_task\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task\agents\rl_games_rough_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\__init__.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\simple_dog_v2_env.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\simple_dog_v2_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_v2/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_v2/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current\simple_dog_current_env.py"; Remote = "$remoteTraining/simple_dog_task_current" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current\simple_dog_current_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current/agents" }
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v4\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v4" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v4\simple_dog_current_body_v4_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v4" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v4\simple_dog_current_body_v4_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v4" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v4\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v4/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v4\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v4/agents" }
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v5\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v5" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v5\simple_dog_current_body_v5_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v5" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v5\simple_dog_current_body_v5_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v5" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v5\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v5/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v5\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v5/agents" }
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v6\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v6" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v6\simple_dog_current_body_v6_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v6" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v6\simple_dog_current_body_v6_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v6" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v6\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v6/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v6\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v6/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v7\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v7" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v7\simple_dog_current_body_v7_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v7" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v7\simple_dog_current_body_v7_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v7" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v7\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v7/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v7\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v7/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v8\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v8" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v8\simple_dog_current_body_v8_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v8" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v8\simple_dog_current_body_v8_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v8" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v8\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v8/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v8\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v8/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v9\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v9" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v9\simple_dog_current_body_v9_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v9" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v9\simple_dog_current_body_v9_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v9" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v9\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v9/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v9\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v9/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v10\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v10" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v10\simple_dog_current_body_v10_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v10" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v10\simple_dog_current_body_v10_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v10" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v10\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v10/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current_body_v10\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v10/agents" }
)
foreach ($copy in $copies) {
    if (-not (Test-Path -LiteralPath $copy.Local -PathType Leaf)) {
        throw "Required training file was not found: $($copy.Local)"
    }
    & scp @sshOptions $copy.Local "${sshTarget}:$($copy.Remote)/"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not deploy: $($copy.Local)"
    }
}
if ($ControlProfile) {
    & scp @sshOptions $resolvedControlProfile "${sshTarget}:/home/leo/isaac-workspace/projects/training/control_profiles/$($validated.profile_id)-$($profileHash.Substring(0, 12)).json"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not deploy the validated control profile."
    }
}
if ($SimulationFit) {
    $remoteFitHostPath = "/home/leo/isaac-workspace/projects/training/fits/$(Split-Path -Leaf $remoteSimulationFit)"
    & scp @sshOptions $resolvedSimulationFit "${sshTarget}:$remoteFitHostPath"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not deploy the validated CurrentV3 simulation fit."
    }
}

& ssh @sshOptions $sshTarget "sed -i 's/\r$//' $remoteTraining/run_simple_dog.sh $remoteTraining/simple-dog-gb10.sh $remoteTraining/run_simple_dog_scratch_seed_queue.sh $remoteTraining/ensure_simple_dog_meshes.sh $remoteTraining/prepare_control_profile_asset.sh $remoteTraining/validate_control_profile_robot.sh $remoteTraining/evaluate_simple_dog_policy.sh && chmod 0755 $remoteTraining/run_simple_dog.sh $remoteTraining/simple-dog-gb10.sh $remoteTraining/run_simple_dog_scratch_seed_queue.sh $remoteTraining/ensure_simple_dog_meshes.sh $remoteTraining/prepare_control_profile_asset.sh $remoteTraining/validate_control_profile_robot.sh $remoteTraining/evaluate_simple_dog_policy.sh"
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare the remote training launcher."
}

if ($ControlProfile) {
    & ssh @sshOptions $sshTarget "docker exec isaac-lab-gb10 bash /workspace/projects/training/prepare_control_profile_asset.sh '$remoteControlProfile'"
    if ($LASTEXITCODE -ne 0) {
        throw "The selected control-profile robot asset could not be prepared for Isaac Lab."
    }

    if (-not $ReuseValidatedRobot) {
        $validationTask = switch ($Terrain) {
            "V2Core" { "Isaac-Locomotion-V2-Core-Simple-Dog-Direct-v0" }
            "V2Robust" { "Isaac-Locomotion-V2-Robust-Simple-Dog-Direct-v0" }
            "V2Goal" { "Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-v0" }
            "V2Rough" { "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0" }
            # Articulation validation is policy-observation agnostic. Use the
            # registered V2 Rough harness for the identical 12-DOF profile;
            # the following smoke/training launch resolves CurrentV3 itself.
            "CurrentV3Core" { "Isaac-Locomotion-V2-Core-Simple-Dog-Direct-v0" }
            "CurrentV3Reverse" { "Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-v0" }
            "CurrentV3ForwardSpecialist" { "Isaac-Locomotion-V2-Core-Simple-Dog-Direct-v0" }
            "CurrentV3ReverseSpecialist" { "Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-v0" }
            "CurrentV3Strafe" { "Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-v0" }
            "CurrentV3Turn" { "Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-v0" }
            "CurrentV3Goal" { "Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-v0" }
            "CurrentV3Posture" { "Isaac-Locomotion-V2-Goal-Simple-Dog-Direct-v0" }
            "CurrentV3Rough" { "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0" }
            "CurrentBodyV4Hard" { "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0" }
            "CurrentBodyV5Hard" { "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0" }
            "CurrentBodyV6Hard" { "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0" }
            "CurrentBodyV7Hard" { "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0" }
            "CurrentBodyV8Hard" { "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0" }
            "CurrentBodyV9Hard" { "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0" }
            "CurrentBodyV10Hard" { "Isaac-Locomotion-V2-Rough-Simple-Dog-Direct-v0" }
            default { throw "Control profiles require a V2, CurrentV3, or CurrentBody training stage." }
        }
        & ssh @sshOptions $sshTarget "docker exec --workdir /workspace/projects/training isaac-lab-gb10 bash /workspace/projects/training/validate_control_profile_robot.sh '$remoteControlProfile' '$validationTask'"
        if ($LASTEXITCODE -ne 0) {
            throw "The selected control-profile robot failed Isaac Lab articulation validation."
        }
    }
    else {
        Write-Host "Reusing the prior passing articulation validation for this unchanged robot mapping."
    }
}
else {
    & ssh @sshOptions $sshTarget "docker exec isaac-lab-gb10 bash /workspace/projects/training/ensure_simple_dog_meshes.sh"
    if ($LASTEXITCODE -ne 0) {
        throw "The legacy eight-joint Onshape geometry could not be prepared for Isaac Lab."
    }
}

$checkpointArg = if ($Checkpoint) { $Checkpoint } else { "''" }
$tuningArg = if ($TuningConfig) { $TuningConfig } else { "''" }
$seedArg = if ($null -ne $Seed) { [string]$Seed } else { "''" }
$runDirectory = (& ssh @sshOptions $sshTarget `
    "$remoteTraining/simple-dog-gb10.sh start $NumEnvs $MaxIterations $checkpointArg $tuningArg $($Terrain.ToLowerInvariant()) '$remoteControlProfile' '$profileHash' '$recordVideo' '$videoInterval' '$videoLength' '$remoteSimulationFit' $seedArg" |
    Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or -not $runDirectory) {
    throw "The detached training process did not create a run directory."
}

Write-Host "Simple-dog training started."
Write-Host "Environments: $NumEnvs"
Write-Host "Iterations:   $MaxIterations"
Write-Host "Terrain:      $Terrain"
if ($null -ne $Seed) {
    Write-Host "Seed:         $Seed"
}
if ($Checkpoint) {
    Write-Host "Checkpoint:   $Checkpoint"
}
if ($TuningConfig) {
    Write-Host "Tuning:       $TuningConfig"
}
if ($ControlProfile) {
    Write-Host "Profile:       $($validated.profile_id)"
    Write-Host "Profile SHA:   $($profileHash.Substring(0, 12))"
}
if ($SimulationFit) {
    Write-Host "Fit:           $remoteSimulationFit"
    Write-Host "Fit SHA:       $($simulationFitHash.Substring(0, 12))"
}
Write-Host "Run data:     $runDirectory"
Write-Host "Status:       .\Get-SimpleDogTrainingStatus.ps1"
Write-Host "Stop/free GPU: .\Stop-SimpleDogTraining.ps1"
