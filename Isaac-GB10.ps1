[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet(
        "setup",
        "start-lab",
        "stop-lab",
        "shell",
        "test-lab",
        "test-robot",
        "start-onshape",
        "stop-onshape",
        "logs-onshape",
        "start-dog-playback",
        "stop-dog-playback",
        "logs-dog-playback",
        "stop-all",
        "status",
        "space",
        "cleanup"
    )]
    [string]$Command = "status",

    [switch]$AcceptNvidiaEula,
    [switch]$AllowPrivateLanStreaming,
    [switch]$ConfirmCleanup,
    [switch]$RemoveImages,
    [switch]$RemoveCaches,
    [ValidatePattern("^/workspace/projects/training/logs/rl_games/simple_dog(_rough)?_velocity_direct/[A-Za-z0-9_./-]+\.pth$")]
    [string]$Checkpoint = "/workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/2026-07-30_01-26-32/nn/simple_dog_velocity_direct.pth",
    [ValidateSet("Flat", "Rough")]
    [string]$Terrain = "Flat",
    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
    [string]$RobotName = "simple-8-joint-dog"
)

$ErrorActionPreference = "Stop"

$sshTarget = "leo@gx10-ddb2.local"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$localHelper = Join-Path $PSScriptRoot "isaac-gb10.sh"
$localLabDockerfile = Join-Path $PSScriptRoot "Dockerfile.isaac-lab-gb10"
$localRobotValidator = Join-Path $PSScriptRoot "validate-onshape-robot.py"
$remoteRoot = "/home/leo/isaac-workspace"
$remoteHelper = "$remoteRoot/bin/isaac-gb10.sh"
$remoteLabDockerfile = "$remoteRoot/bin/Dockerfile.isaac-lab-gb10"
$remoteRobotValidator = "$remoteRoot/projects/training/tools/validate-onshape-robot.py"
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
if (-not (Test-Path -LiteralPath $localHelper -PathType Leaf)) {
    throw "Local Isaac helper was not found: $localHelper"
}
if (-not (Test-Path -LiteralPath $localLabDockerfile -PathType Leaf)) {
    throw "Local Isaac Lab runtime Dockerfile was not found: $localLabDockerfile"
}
if (-not (Test-Path -LiteralPath $localRobotValidator -PathType Leaf)) {
    throw "Local Onshape robot validator was not found: $localRobotValidator"
}

$identity = (& ssh @sshOptions $sshTarget "whoami").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}

& ssh @sshOptions $sshTarget "install -d -m 0755 $remoteRoot/bin $remoteRoot/projects/training/tools"
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare the remote Isaac helper directory."
}
& scp @sshOptions $localHelper "${sshTarget}:$remoteHelper"
if ($LASTEXITCODE -ne 0) {
    throw "Could not copy the Isaac helper to the GB10."
}
& scp @sshOptions $localLabDockerfile "${sshTarget}:$remoteLabDockerfile"
if ($LASTEXITCODE -ne 0) {
    throw "Could not copy the Isaac Lab runtime Dockerfile to the GB10."
}
& scp @sshOptions $localRobotValidator "${sshTarget}:$remoteRobotValidator"
if ($LASTEXITCODE -ne 0) {
    throw "Could not copy the Onshape robot validator to the GB10."
}
& ssh @sshOptions $sshTarget "chmod 0755 $remoteHelper"
if ($LASTEXITCODE -ne 0) {
    throw "Could not mark the remote Isaac helper executable."
}

$remoteArgs = @($Command)
switch ($Command) {
    "setup" {
        if (-not $AcceptNvidiaEula) {
            throw "Setup requires Leo's explicit acceptance. Review NVIDIA's Omniverse license, then rerun with -AcceptNvidiaEula."
        }
        $remoteArgs += "--accept-nvidia-eula"
    }
    "start-onshape" {
        if (-not $AllowPrivateLanStreaming) {
            throw "Onshape import needs unauthenticated WebRTC on the private LAN. Rerun with -AllowPrivateLanStreaming if that boundary is acceptable."
        }
        $remoteArgs += "--allow-private-lan-streaming"
    }
    "start-dog-playback" {
        if (-not $AllowPrivateLanStreaming) {
            throw "Dog playback needs unauthenticated WebRTC on the private LAN. Rerun with -AllowPrivateLanStreaming if that boundary is acceptable."
        }
        $remoteArgs += "--allow-private-lan-streaming"
        $remoteArgs += $Checkpoint
        $remoteArgs += $Terrain.ToLowerInvariant()
    }
    "test-robot" {
        $remoteArgs += $RobotName
    }
    "cleanup" {
        if (-not $ConfirmCleanup) {
            throw "Cleanup requires -ConfirmCleanup."
        }
        if (-not $RemoveImages -and -not $RemoveCaches) {
            throw "Choose -RemoveImages and/or -RemoveCaches. Project data is always preserved."
        }
        $remoteArgs += "--confirmed"
        if ($RemoveImages) {
            $remoteArgs += "--images"
        }
        if ($RemoveCaches) {
            $remoteArgs += "--caches"
        }
    }
}

$remoteCommand = "$remoteHelper " + ($remoteArgs -join " ")
if ($Command -eq "shell") {
    & ssh -t @sshOptions $sshTarget $remoteCommand
}
else {
    & ssh @sshOptions $sshTarget $remoteCommand
}
if ($LASTEXITCODE -ne 0) {
    throw "Isaac command failed: $Command"
}
