[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidatePattern('^/workspace/projects/training/logs/rl_games/(simple_dog_v2_locomotion_direct|quadruped_v2_[A-Za-z0-9_-]+|simple_dog_current_v3_rough_direct|quadruped_current_v3_[A-Za-z0-9_-]+|quadruped_current_body_v(?:17|18|19|20)_[A-Za-z0-9_-]+)/[A-Za-z0-9_./-]+\.pth$')]
    [string]$Checkpoint,

    [ValidateSet("Core", "Robust", "Goal", "Rough", "CurrentFlat", "Current", "CurrentStress", "CurrentBodyV17", "CurrentBodyV18", "CurrentBodyV19", "CurrentBodyV17Push", "CurrentBodyV18Push", "CurrentBodyV19Push", "DeliveryFlat", "Delivery", "DeliveryStress")]
    [string]$Stage = "Core",

    [switch]$ScreenOnly,

    [ValidateRange(0, 2147483647)]
    [int]$Seed = 42,

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
if (($Stage -like "Current*" -or $Stage -like "Delivery*")) {
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
    @{ Local = Join-Path $localTraining "delivery_contract.py"; Remote = $remoteTraining },
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
# CurrentBody task registration imports earlier family modules. Copy only
# source/config files, preserving checkpoints and all generated evidence.
if ($Stage -like "CurrentBody*") {
    $copies += @{ Local = Join-Path $localTraining "v4_difficulty_ramp.py"; Remote = $remoteTraining }
    $bodyDirectories = @()
    foreach ($version in 4..19) {
        $family = "simple_dog_task_current_body_v$version"
        $familyRoot = Join-Path $localTraining $family
        foreach ($file in Get-ChildItem -LiteralPath $familyRoot -Recurse -File |
            Where-Object { $_.Extension -in @(".py", ".yaml") -and $_.FullName -notmatch '[\\/]__pycache__[\\/]' }) {
            $relative = $file.FullName.Substring($localTraining.Length + 1).Replace('\', '/')
            $destination = "$remoteTraining/" + $relative.Substring(0, $relative.LastIndexOf('/'))
            $bodyDirectories += $destination
            $copies += @{ Local = $file.FullName; Remote = $destination }
        }
    }
    $directoryArgs = ($bodyDirectories | Sort-Object -Unique) -join " "
    & ssh @sshOptions $sshTarget "install -d -m 0755 $directoryArgs"
    if ($LASTEXITCODE -ne 0) { throw "Could not prepare CurrentBody evaluation directories." }
}
if ($Stage -like "Delivery*") {
    if ($ScreenOnly) { throw "Delivery certification includes exact-checkpoint video; omit ScreenOnly." }
    if ($Checkpoint -notmatch '/quadruped_current_body_v20_') { throw "Delivery requires its V20 checkpoint." }
    foreach ($name in @("train_simple_dog.py", "simple_dog_tuning.py", "v4_difficulty_ramp.py", "deployable_dynamics.py", "snapshot_delivery_run.py", "evaluate_delivery_policy.py")) {
        $copies += @{ Local = Join-Path $localTraining $name; Remote = $remoteTraining }
    }
    $copies += @{ Local = Join-Path $localTraining "fits/servo-response-20260829.json"; Remote = "$remoteTraining/fits" }
    $directories = @()
    foreach ($family in @("simple_dog_task", "simple_dog_task_v2", "simple_dog_task_current_body_v4", "simple_dog_task_current_body_v20")) {
        foreach ($file in Get-ChildItem -LiteralPath (Join-Path $localTraining $family) -Recurse -File |
            Where-Object { $_.Extension -in @(".py", ".yaml") }) {
            $relative = $file.FullName.Substring($localTraining.Length + 1).Replace('\', '/')
            $destination = "$remoteTraining/" + $relative.Substring(0, $relative.LastIndexOf('/'))
            $directories += $destination
            $copies += @{ Local = $file.FullName; Remote = $destination }
        }
    }
    & ssh @sshOptions $sshTarget ("install -d -m 0755 " + (($directories | Sort-Object -Unique) -join " "))
    if ($LASTEXITCODE -ne 0) { throw "Could not prepare delivery source directories." }
}
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

if ($Stage -like "Delivery*") {
    $suite = $Stage.ToLowerInvariant()
    & ssh @sshOptions $sshTarget "docker exec --workdir /workspace/isaaclab isaac-lab-gb10 /workspace/isaaclab/_isaac_sim/kit/python/bin/python3 /workspace/projects/training/evaluate_delivery_policy.py '$Checkpoint' --profile '$remoteProfile' --fit '$remoteSimulationFit' --suite '$suite' --seed $Seed"
    if ($LASTEXITCODE -ne 0) { throw "$Stage evaluation rejected the candidate; see its evidence directory." }
    return
}

$stageName = $Stage.ToLowerInvariant()
$recordVideo = if ($ScreenOnly) { "0" } else { "1" }
$qualityGate = if ($RequireGaitQuality -or $Stage -eq "Rough" -or ($Stage -like "Current*" -or $Stage -like "Delivery*")) { "1" } else { "0" }
& ssh @sshOptions $sshTarget `
    "docker exec --workdir /workspace/isaaclab isaac-lab-gb10 /workspace/projects/training/evaluate_simple_dog_policy.sh '$Checkpoint' '$stageName' '$remoteProfile' '$recordVideo' '$qualityGate' '$remoteSimulationFit'"
if ($LASTEXITCODE -ne 0) {
    throw "$Stage deterministic policy evaluation failed. See the printed evidence directory."
}
