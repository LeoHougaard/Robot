[CmdletBinding()]
param(
    [switch]$AllowPrivateLanStreaming
)

if (-not $AllowPrivateLanStreaming) {
    throw "Rerun with -AllowPrivateLanStreaming after accepting that Isaac WebRTC has no authentication or encryption on the private LAN."
}

& (Join-Path $PSScriptRoot "Isaac-GB10.ps1") start-onshape -AllowPrivateLanStreaming
