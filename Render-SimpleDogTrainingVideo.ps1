[CmdletBinding()]
param(
    [ValidateRange(100, 3000)]
    [int]$VideoLength = 400
)

$ErrorActionPreference = "Stop"
$sshTarget = "leo@gx10-ddb2.local"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$remoteTraining = "/home/leo/isaac-workspace/projects/training"
$remoteHelper = "$remoteTraining/simple-dog-gb10.sh"
$sshOptions = @(
    "-i", $keyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=yes"
)

if (-not (Test-Path -LiteralPath $keyPath -PathType Leaf)) {
    throw "NVIDIA Sync SSH key was not found."
}
$identity = ((& ssh -n @sshOptions $sshTarget "whoami") -join "").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}

$copies = @(
    @{ Local = "training\simple-dog-gb10.sh"; Remote = $remoteTraining },
    @{ Local = "training\render_simple_dog_playback.sh"; Remote = $remoteTraining },
    @{ Local = "training\play_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = "training\robot_control_profile.py"; Remote = $remoteTraining },
    @{ Local = "training\simple_dog_task_v2\simple_dog_v2_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_v2" }
)
foreach ($copy in $copies) {
    $localPath = Join-Path $PSScriptRoot $copy.Local
    if (-not (Test-Path -LiteralPath $localPath -PathType Leaf)) {
        throw "Required rollout file is missing: $localPath"
    }
    & scp @sshOptions $localPath "${sshTarget}:$($copy.Remote)/"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not deploy rollout file: $localPath"
    }
}
& ssh -n @sshOptions $sshTarget "chmod 0755 '$remoteHelper' '$remoteTraining/render_simple_dog_playback.sh' && '$remoteHelper' render-latest-video '$VideoLength'"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
