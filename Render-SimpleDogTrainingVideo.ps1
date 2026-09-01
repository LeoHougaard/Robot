[CmdletBinding()]
param(
    [ValidateRange(100, 3000)]
    [int]$VideoLength = 400,

    [ValidateRange(0, 4)]
    [int]$ValidationSample = 0
)

$ErrorActionPreference = "Stop"
$sshHost = if ($env:ROBOT_GB10_HOST) { $env:ROBOT_GB10_HOST.Trim() } else { "gx10-ddb2.local" }
if ($sshHost -notmatch '^[A-Za-z0-9.-]+$') {
    throw "ROBOT_GB10_HOST must be a hostname or IPv4 address."
}
$sshTarget = "leo@$sshHost"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$remoteTraining = "/home/leo/isaac-workspace/projects/training"
$remoteHelper = "$remoteTraining/simple-dog-gb10.sh"
$sshOptions = @(
    "-i", $keyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=5",
    "-o", "ServerAliveCountMax=2",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "HostKeyAlias=gx10-ddb2.local"
)

if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    throw "NVIDIA Sync SSH key was not found."
}
$identity = ((& ssh -n @sshOptions $sshTarget "whoami") -join "").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}

$rolloutDirectories = @(
    "$remoteTraining/simple_dog_task_current_body_v8",
    "$remoteTraining/simple_dog_task_current_body_v8/agents",
    "$remoteTraining/simple_dog_task_current_body_v9",
    "$remoteTraining/simple_dog_task_current_body_v9/agents"
)
& ssh -n @sshOptions $sshTarget ("install -d -m 0755 " + ($rolloutDirectories -join " "))
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare remote rollout directories."
}

$copies = @(
    @{ Local = "training\simple-dog-gb10.sh"; Remote = $remoteTraining },
    @{ Local = "training\render_simple_dog_playback.sh"; Remote = $remoteTraining },
    @{ Local = "training\play_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = "training\pose_goal_controller.py"; Remote = $remoteTraining },
    @{ Local = "training\terrain_curriculum.py"; Remote = $remoteTraining },
    @{ Local = "training\video_camera.py"; Remote = $remoteTraining },
    @{ Local = "training\robot_control_profile.py"; Remote = $remoteTraining },
    @{ Local = "training\current_policy_fit.py"; Remote = $remoteTraining },
    @{ Local = "training\simple_dog_task\simple_dog_env.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = "training\simple_dog_task\simple_dog_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = "training\simple_dog_task_v2\simple_dog_v2_env.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = "training\simple_dog_task_v2\simple_dog_v2_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = "training\simple_dog_task_current\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current" },
    @{ Local = "training\simple_dog_task_current\simple_dog_current_env.py"; Remote = "$remoteTraining/simple_dog_task_current" },
    @{ Local = "training\simple_dog_task_current\simple_dog_current_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current" },
    @{ Local = "training\simple_dog_task_current_body_v4\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v4" },
    @{ Local = "training\simple_dog_task_current_body_v4\simple_dog_current_body_v4_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v4" },
    @{ Local = "training\simple_dog_task_current_body_v4\simple_dog_current_body_v4_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v4" },
    @{ Local = "training\simple_dog_task_current_body_v4\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v4/agents" },
    @{ Local = "training\simple_dog_task_current_body_v4\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v4/agents" },
    @{ Local = "training\simple_dog_task_current_body_v5\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v5" },
    @{ Local = "training\simple_dog_task_current_body_v5\simple_dog_current_body_v5_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v5" },
    @{ Local = "training\simple_dog_task_current_body_v5\simple_dog_current_body_v5_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v5" },
    @{ Local = "training\simple_dog_task_current_body_v5\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v5/agents" },
    @{ Local = "training\simple_dog_task_current_body_v5\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v5/agents" }
    @{ Local = "training\simple_dog_task_current_body_v6\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v6" },
    @{ Local = "training\simple_dog_task_current_body_v6\simple_dog_current_body_v6_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v6" },
    @{ Local = "training\simple_dog_task_current_body_v6\simple_dog_current_body_v6_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v6" },
    @{ Local = "training\simple_dog_task_current_body_v6\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v6/agents" },
    @{ Local = "training\simple_dog_task_current_body_v6\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v6/agents" },
    @{ Local = "training\simple_dog_task_current_body_v7\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v7" },
    @{ Local = "training\simple_dog_task_current_body_v7\simple_dog_current_body_v7_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v7" },
    @{ Local = "training\simple_dog_task_current_body_v7\simple_dog_current_body_v7_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v7" },
    @{ Local = "training\simple_dog_task_current_body_v7\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v7/agents" },
    @{ Local = "training\simple_dog_task_current_body_v7\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v7/agents" },
    @{ Local = "training\simple_dog_task_current_body_v8\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v8" },
    @{ Local = "training\simple_dog_task_current_body_v8\simple_dog_current_body_v8_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v8" },
    @{ Local = "training\simple_dog_task_current_body_v8\simple_dog_current_body_v8_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v8" },
    @{ Local = "training\simple_dog_task_current_body_v8\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v8/agents" },
    @{ Local = "training\simple_dog_task_current_body_v8\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v8/agents" },
    @{ Local = "training\simple_dog_task_current_body_v9\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v9" },
    @{ Local = "training\simple_dog_task_current_body_v9\simple_dog_current_body_v9_env.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v9" },
    @{ Local = "training\simple_dog_task_current_body_v9\simple_dog_current_body_v9_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v9" },
    @{ Local = "training\simple_dog_task_current_body_v9\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_current_body_v9/agents" },
    @{ Local = "training\simple_dog_task_current_body_v9\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_current_body_v9/agents" }
)
foreach ($copy in $copies) {
    $localPath = Join-Path $PSScriptRoot $copy.Local
    if (-not (Test-Path -LiteralPath $localPath -PathType Leaf)) {
        throw "Required rollout file is missing: $localPath"
    }
    $copied = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        & scp @sshOptions $localPath "${sshTarget}:$($copy.Remote)/"
        if ($LASTEXITCODE -eq 0) {
            $copied = $true
            break
        }
        if ($attempt -lt 3) {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $copied) {
        throw "Could not deploy rollout file: $localPath"
    }
}
& ssh -n @sshOptions $sshTarget "chmod 0755 '$remoteHelper' '$remoteTraining/render_simple_dog_playback.sh' && '$remoteHelper' render-latest-video '$VideoLength' '$ValidationSample'"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
