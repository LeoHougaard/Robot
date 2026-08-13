[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8765,

    [ValidatePattern("^[A-Za-z0-9_-]+$")]
    [string]$Profile = "assembly-1-12dof",

    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$python = (Get-Command python -ErrorAction Stop).Source
$arguments = @("-m", "control_center.server", "--port", $Port, "--profile", $Profile)
if ($NoBrowser) {
    $arguments += "--no-browser"
}
Push-Location $PSScriptRoot
try {
    & $python @arguments
}
finally {
    Pop-Location
}
