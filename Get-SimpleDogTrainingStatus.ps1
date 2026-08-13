[CmdletBinding()]
param()

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
