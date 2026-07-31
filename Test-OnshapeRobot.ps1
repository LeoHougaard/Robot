[CmdletBinding()]
param(
    [ValidatePattern("^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
    [string]$RobotName = "simple-8-joint-dog"
)

& (Join-Path $PSScriptRoot "Isaac-GB10.ps1") test-robot -RobotName $RobotName
if ($LASTEXITCODE -ne 0) {
    throw "Onshape robot validation failed."
}
