[CmdletBinding()]
param()

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

$identityOutput = & ssh -n @sshOptions $sshTarget "whoami"
$identity = ($identityOutput -join "").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Could not verify the GB10 as user leo. Check that gx10-ddb2.local is reachable."
}

$remoteHelper = "/home/leo/isaac-workspace/projects/training/simple-dog-gb10.sh"
& ssh @sshOptions $sshTarget "test -x $remoteHelper && $remoteHelper status"
if ($LASTEXITCODE -ne 0) {
    throw "Could not read simple-dog training status."
}
