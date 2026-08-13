[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^/workspace/projects/training/logs/rl_games/(simple_dog_v2_locomotion_direct|quadruped_v2_[A-Za-z0-9_-]+)/[A-Za-z0-9_./-]+\.pth$')]
    [string]$Checkpoint,

    [ValidateSet("Core", "Robust", "Goal")]
    [string]$Stage = "Core",

    [switch]$ScreenOnly,

    [switch]$RequireGaitQuality,

    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$ControlProfile
)

$ErrorActionPreference = "Stop"
$sshTarget = "leo@gx10-ddb2.local"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$remoteTraining = "/home/leo/isaac-workspace/projects/training"
$localTraining = Join-Path $PSScriptRoot "training"
$sshOptions = @(
    "-i", $keyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=yes"
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

$copies = @(
    @{ Local = Join-Path $localTraining "play_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "robot_control_profile.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "evaluate_simple_dog_policy.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "evaluate_simple_dog_policy.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\__init__.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\simple_dog_v2_env.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\simple_dog_v2_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_v2" }
)
foreach ($copy in $copies) {
    & scp @sshOptions $copy.Local "${sshTarget}:$($copy.Remote)/"
    if ($LASTEXITCODE -ne 0) { throw "Could not deploy $($copy.Local)." }
}
& scp @sshOptions $resolvedProfile "${sshTarget}:/home/leo/isaac-workspace/projects/training/control_profiles/$($validation.profile_id)-$($profileHash.Substring(0, 12)).json"
if ($LASTEXITCODE -ne 0) { throw "Could not deploy the control profile." }

& ssh @sshOptions $sshTarget `
    "sed -i 's/\r$//' $remoteTraining/evaluate_simple_dog_policy.sh && chmod 0755 $remoteTraining/evaluate_simple_dog_policy.sh && docker exec isaac-lab-gb10 test -f '$Checkpoint'"
if ($LASTEXITCODE -ne 0) { throw "The checkpoint does not exist in Isaac Lab." }

$stageName = $Stage.ToLowerInvariant()
$recordVideo = if ($ScreenOnly) { "0" } else { "1" }
$qualityGate = if ($RequireGaitQuality) { "1" } else { "0" }
& ssh @sshOptions $sshTarget `
    "docker exec --workdir /workspace/isaaclab isaac-lab-gb10 /workspace/projects/training/evaluate_simple_dog_policy.sh '$Checkpoint' '$stageName' '$remoteProfile' '$recordVideo' '$qualityGate'"
if ($LASTEXITCODE -ne 0) {
    throw "$Stage deterministic policy evaluation failed. See the printed evidence directory."
}
