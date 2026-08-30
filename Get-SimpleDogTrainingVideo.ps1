[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Destination,

    [string]$ExpectedExperiment = ""
)

$ErrorActionPreference = "Stop"
$sshHost = if ($env:ROBOT_GB10_HOST) { $env:ROBOT_GB10_HOST.Trim() } else { "gx10-ddb2.local" }
if ($sshHost -notmatch '^[A-Za-z0-9.-]+$') {
    throw "ROBOT_GB10_HOST must be a hostname or IPv4 address."
}
$sshTarget = "leo@$sshHost"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
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
$identity = ((& ssh -n @sshOptions $sshTarget "whoami") -join "").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}
$remoteHelper = "/home/leo/isaac-workspace/projects/training/simple-dog-gb10.sh"
$remoteVideo = ((& ssh -n @sshOptions $sshTarget "test -x $remoteHelper && $remoteHelper latest-video") -join "").Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect training videos."
}
if (-not $remoteVideo) {
    throw "No completed training video is available yet."
}
if ($remoteVideo -notmatch '^/home/leo/isaac-workspace/projects/training/logs/rl_games/[A-Za-z0-9_./-]+\.mp4$') {
    throw "The remote video path was outside the training log directory."
}
if ($ExpectedExperiment) {
    if ($ExpectedExperiment -notmatch '^[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}$') {
        throw "ExpectedExperiment was not a valid training experiment name."
    }
    if ($remoteVideo -notmatch "/$([regex]::Escape($ExpectedExperiment))/") {
        throw "The active run has not completed its first rollout video yet."
    }
}
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$destinationParent = Split-Path -Parent $destinationPath
New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
& scp @sshOptions "${sshTarget}:$remoteVideo" $destinationPath
if ($LASTEXITCODE -ne 0) {
    throw "Could not copy the latest training video."
}
Write-Output "Copied video: $destinationPath"
Write-Output "Source video: $remoteVideo"
