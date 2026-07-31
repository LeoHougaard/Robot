[CmdletBinding()]
param()

& (Join-Path $PSScriptRoot "Isaac-GB10.ps1") status
& (Join-Path $PSScriptRoot "Isaac-GB10.ps1") logs-dog-playback
