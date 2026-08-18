[CmdletBinding()]
param(
    [switch]$AllowPrivateLanStreaming,

    [ValidateSet("Flat", "Rough", "V2Core", "V2Rough")]
    [string]$Terrain = "Flat",

    [ValidatePattern("^/workspace/projects/training/logs/rl_games/(simple_dog_(rough_)?velocity_direct|simple_dog_v2_locomotion_direct)/[A-Za-z0-9_./-]+\.pth$")]
    [string]$Checkpoint = "/workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/2026-07-30_01-26-32/nn/simple_dog_velocity_direct.pth"
)

$ErrorActionPreference = "Stop"

if (-not $AllowPrivateLanStreaming) {
    throw "Rerun with -AllowPrivateLanStreaming after accepting that Isaac WebRTC has no authentication or encryption on the private LAN."
}

$isV2Terrain = $Terrain -in @("V2Core", "V2Rough")
$isV2Checkpoint = $Checkpoint -match '^/workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/'
if ($isV2Terrain -ne $isV2Checkpoint) {
    throw "V1 and V2 playback tasks require checkpoints with matching observation layouts."
}

$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$sshTarget = "leo@gx10-ddb2.local"
$remoteTraining = "/home/leo/isaac-workspace/projects/training"
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

$trainingActive = (& ssh @sshOptions $sshTarget "docker exec isaac-lab-gb10 pgrep -f '[t]rain_simple_dog.py' >/dev/null 2>&1 && printf active || true") -join ""
if ($trainingActive.Trim()) {
    throw "Simple Dog training is active. Stop it explicitly before starting policy playback."
}

& ssh @sshOptions $sshTarget "install -d -m 0755 '$remoteTraining/simple_dog_task' '$remoteTraining/simple_dog_task/agents' '$remoteTraining/simple_dog_task_v2' '$remoteTraining/simple_dog_task_v2/agents' '$remoteTraining/assets'"
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare remote playback directories."
}

$copies = @(
    @{ Local = "training\play_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = "training\pose_goal_controller.py"; Remote = $remoteTraining },
    @{ Local = "training\simple_dog_tuning.py"; Remote = $remoteTraining },
    @{ Local = "training\simple_dog_task\__init__.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = "training\simple_dog_task\simple_dog_env.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = "training\simple_dog_task\simple_dog_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = "training\simple_dog_task\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = "training\simple_dog_task\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = "training\simple_dog_task\agents\rl_games_rough_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = "training\simple_dog_task_v2\__init__.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = "training\simple_dog_task_v2\simple_dog_v2_env.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = "training\simple_dog_task_v2\simple_dog_v2_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = "training\simple_dog_task_v2\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_v2/agents" },
    @{ Local = "training\simple_dog_task_v2\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_v2/agents" },
    @{ Local = "training\assets\simple_dog_training.usda"; Remote = "$remoteTraining/assets" }
)
foreach ($copy in $copies) {
    $localPath = Join-Path $PSScriptRoot $copy.Local
    if (-not (Test-Path -LiteralPath $localPath -PathType Leaf)) {
        throw "Required playback file is missing: $localPath"
    }
    & scp @sshOptions $localPath "${sshTarget}:$($copy.Remote)/"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not deploy playback file: $localPath"
    }
}

& (Join-Path $PSScriptRoot "Stop-IsaacOnshape.ps1")
& (Join-Path $PSScriptRoot "Stop-IsaacLab.ps1")
& (Join-Path $PSScriptRoot "Isaac-GB10.ps1") start-dog-playback `
    -AllowPrivateLanStreaming `
    -Checkpoint $Checkpoint `
    -Terrain $Terrain
