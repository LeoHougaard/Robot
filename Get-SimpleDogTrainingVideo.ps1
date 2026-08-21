[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$Destination
)

$ErrorActionPreference = "Stop"
$sshTarget = "leo@gx10-ddb2.local"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
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
$remoteMetadata = "$remoteVideo.metadata.json"
& ssh -n @sshOptions $sshTarget "test -f '$remoteMetadata'"
if ($LASTEXITCODE -ne 0) {
    throw "The newest training video has no profile/task metadata and will not be shown."
}
$destinationPath = [System.IO.Path]::GetFullPath($Destination)
$destinationParent = Split-Path -Parent $destinationPath
New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
& scp @sshOptions "${sshTarget}:$remoteVideo" $destinationPath
if ($LASTEXITCODE -ne 0) {
    throw "Could not copy the latest training video."
}
& scp @sshOptions "${sshTarget}:$remoteMetadata" "$destinationPath.metadata.json"
if ($LASTEXITCODE -ne 0) {
    throw "Could not copy the training video metadata."
}
Write-Output $destinationPath
