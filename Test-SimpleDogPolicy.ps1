[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^/workspace/projects/training/logs/rl_games/(simple_dog_v2_locomotion_direct|quadruped_v2_[A-Za-z0-9_-]+|simple_dog_current_v3_rough_direct|quadruped_current_v3_[A-Za-z0-9_-]+)/[A-Za-z0-9_./-]+\.pth$')]
    [string]$Checkpoint,

    [ValidateSet("Core", "Robust", "Goal", "Rough", "CurrentFlat", "Current", "CurrentStress")]
    [string]$Stage = "Core",

    [switch]$ScreenOnly,

    [switch]$RequireGaitQuality,

    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$ControlProfile,

    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$SimulationFit = ""
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
$remoteTraining = "/home/leo/isaac-workspace/projects/training"
$localTraining = Join-Path $PSScriptRoot "training"
$remoteSimulationFit = ""
$sshOptions = @(
    "-i", $keyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "HostKeyAlias=gx10-ddb2.local"
)

$identity = (& ssh @sshOptions $sshTarget "whoami").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}
$workload = (& ssh @sshOptions $sshTarget `
    "docker exec isaac-lab-gb10 pgrep -af '[t]rain_simple_dog.py|[p]lay_simple_dog.py' || true") -join "`n"
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the current Isaac Lab workload."
}
if ($workload.Trim()) {
    throw "Isaac training or playback is active; deterministic evaluation was not started."
}

# A completed headless Isaac run can leave a zero-byte per-user Hub lock even
# though no Hub process remains. SimulationApp then waits indefinitely before
# the first evaluation segment. Remove only the container user's lock, and
# preserve it if any other process still has Hub in its command line.
$clearStaleHubLock = "docker exec --user 0 isaac-lab-gb10 sh -c 'if ! pgrep -x omni.hub >/dev/null 2>&1 && ! pgrep -x omni-hub >/dev/null 2>&1 && ! pgrep -x hub >/dev/null 2>&1 && ! pgrep -x hub-daemon >/dev/null 2>&1; then rm -f -- /tmp/hub-leo.lock /tmp/hub-isaac-sim.lock; fi'"
& ssh @sshOptions $sshTarget $clearStaleHubLock
if ($LASTEXITCODE -ne 0) {
    throw "Could not check the stale Isaac Hub lock before evaluation."
}

$resolvedProfile = (Resolve-Path -LiteralPath $ControlProfile).Path
Push-Location $PSScriptRoot
try {
    $validation = python -m control_center.validate_profile $resolvedProfile | ConvertFrom-Json
}
finally {
    Pop-Location
}
if (-not $validation.ok) {
    throw "Control profile validation failed: $($validation.errors -join '; ')"
}
$profileHash = $validation.hash
$remoteProfile = "/workspace/projects/training/control_profiles/$($validation.profile_id)-$($profileHash.Substring(0, 12)).json"
if ($Stage -in @("CurrentFlat", "Current", "CurrentStress")) {
    if (-not $SimulationFit) {
        throw "Current evaluation requires the provenance-bearing simulation fit."
    }
    $resolvedSimulationFit = (Resolve-Path -LiteralPath $SimulationFit).Path
    $fitHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $resolvedSimulationFit).Hash.ToLowerInvariant()
    $remoteSimulationFit = "/workspace/projects/training/fits/current-v3-$($fitHash.Substring(0, 12)).json"
}
elseif ($SimulationFit) {
    throw "SimulationFit is accepted only for Current evaluation."
}

& ssh @sshOptions $sshTarget "install -d -m 0755 $remoteTraining/simple_dog_task_current $remoteTraining/simple_dog_task_current/agents $remoteTraining/fits"
if ($LASTEXITCODE -ne 0) { throw "Could not prepare CurrentV3 evaluation directories." }

$copies = @(
    @{ Local = Join-Path $localTraining "play_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "pose_goal_controller.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "terrain_curriculum.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "video_camera.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "robot_control_profile.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "evaluate_simple_dog_policy.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "evaluate_simple_dog_policy.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "current_policy_fit.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "simple_dog_task\simple_dog_env.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = Join-Path $localTraining "simple_dog_task\simple_dog_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\__init__.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\simple_dog_v2_env.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\simple_dog_v2_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current\simple_dog_current_env.py"; Remote = "$remoteTraining/simple_dog_task_current" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current\simple_dog_current_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_current\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current/agents" }
)
foreach ($copy in $copies) {
    & scp @sshOptions $copy.Local "${sshTarget}:$($copy.Remote)/"
    if ($LASTEXITCODE -ne 0) { throw "Could not deploy $($copy.Local)." }
}
& scp @sshOptions $resolvedProfile "${sshTarget}:/home/leo/isaac-workspace/projects/training/control_profiles/$($validation.profile_id)-$($profileHash.Substring(0, 12)).json"
if ($LASTEXITCODE -ne 0) { throw "Could not deploy the control profile." }
if ($SimulationFit) {
    $remoteFitHostPath = "/home/leo/isaac-workspace/projects/training/fits/$(Split-Path -Leaf $remoteSimulationFit)"
    & scp @sshOptions $resolvedSimulationFit "${sshTarget}:$remoteFitHostPath"
    if ($LASTEXITCODE -ne 0) { throw "Could not deploy the CurrentV3 simulation fit." }
}

& ssh @sshOptions $sshTarget `
    "sed -i 's/\r$//' $remoteTraining/evaluate_simple_dog_policy.sh && chmod 0755 $remoteTraining/evaluate_simple_dog_policy.sh && docker exec isaac-lab-gb10 test -f '$Checkpoint'"
if ($LASTEXITCODE -ne 0) { throw "The checkpoint does not exist in Isaac Lab." }

$stageName = $Stage.ToLowerInvariant()
$recordVideo = if ($ScreenOnly) { "0" } else { "1" }
$qualityGate = if ($RequireGaitQuality -or $Stage -in @("Rough", "CurrentFlat", "Current", "CurrentStress")) { "1" } else { "0" }
& ssh @sshOptions $sshTarget `
    "docker exec --workdir /workspace/isaaclab isaac-lab-gb10 /workspace/projects/training/evaluate_simple_dog_policy.sh '$Checkpoint' '$stageName' '$remoteProfile' '$recordVideo' '$qualityGate' '$remoteSimulationFit'"
if ($LASTEXITCODE -ne 0) {
    throw "$Stage deterministic policy evaluation failed. See the printed evidence directory."
}
