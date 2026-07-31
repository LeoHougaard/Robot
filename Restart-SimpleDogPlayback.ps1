[CmdletBinding()]
param(
    [switch]$AllowPrivateLanStreaming,
    [ValidateSet("Flat", "Rough")]
    [string]$Terrain = "Flat",
    [ValidatePattern("^/workspace/projects/training/logs/rl_games/simple_dog(_rough)?_velocity_direct/[A-Za-z0-9_./-]+\.pth$")]
    [string]$Checkpoint = "/workspace/projects/training/logs/rl_games/simple_dog_velocity_direct/2026-07-30_01-26-32/nn/simple_dog_velocity_direct.pth"
)

$ErrorActionPreference = "Stop"

if (-not $AllowPrivateLanStreaming) {
    throw "Rerun with -AllowPrivateLanStreaming after accepting that Isaac WebRTC has no authentication or encryption on the private LAN."
}

& (Join-Path $PSScriptRoot "Stop-SimpleDogPlayback.ps1")
& (Join-Path $PSScriptRoot "Start-SimpleDogPlayback.ps1") -AllowPrivateLanStreaming -Terrain $Terrain -Checkpoint $Checkpoint
