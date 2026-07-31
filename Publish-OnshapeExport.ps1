[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateScript({ Test-Path -LiteralPath $_ -PathType Leaf })]
    [string]$ZipPath,

    [Parameter(Mandatory)]
    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
    [string]$RobotName
)

$ErrorActionPreference = "Stop"
$resolvedZip = (Resolve-Path -LiteralPath $ZipPath).Path

Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive = [System.IO.Compression.ZipFile]::OpenRead($resolvedZip)
try {
    $hasRootRobot = $false
    foreach ($entry in $archive.Entries) {
        $name = $entry.FullName
        if (
            $name.StartsWith("/") -or
            $name.StartsWith("\") -or
            $name -match "^[A-Za-z]:" -or
            $name -match "(^|/)\.\.(/|$)" -or
            $name.Contains("\")
        ) {
            throw "The ZIP contains an unsafe path: $name"
        }
        if ($name -ceq "robot.usda") {
            $hasRootRobot = $true
        }
    }
    if (-not $hasRootRobot) {
        throw "This is not an Onshape Omniverse Publisher export: robot.usda is missing at the ZIP root."
    }
}
finally {
    $archive.Dispose()
}

# This also verifies identity and deploys the current, syntax-checked helper.
& (Join-Path $PSScriptRoot "Isaac-GB10.ps1") status | Out-Null

$sshTarget = "leo@gx10-ddb2.local"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$remoteRoot = "/home/leo/isaac-workspace"
$remoteHelper = "$remoteRoot/bin/isaac-gb10.sh"
$remoteZip = "$remoteRoot/incoming/$([Guid]::NewGuid().ToString('N')).zip"
$sshOptions = @(
    "-i", $keyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=yes"
)

& ssh @sshOptions $sshTarget "install -d -m 0755 $remoteRoot/incoming"
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare the remote incoming directory."
}
& scp @sshOptions $resolvedZip "${sshTarget}:$remoteZip"
if ($LASTEXITCODE -ne 0) {
    throw "Could not upload the Onshape export."
}
& ssh @sshOptions $sshTarget "$remoteHelper install-export $RobotName $remoteZip"
if ($LASTEXITCODE -ne 0) {
    throw "The GB10 rejected or could not install the Onshape export."
}
