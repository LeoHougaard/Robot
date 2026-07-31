[CmdletBinding()]
param()

& (Join-Path $PSScriptRoot "Isaac-GB10.ps1") stop-dog-playback
