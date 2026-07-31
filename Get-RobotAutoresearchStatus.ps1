[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$sshOptions = @(
    "-i", $keyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=yes"
)
$remoteRoot = "/home/leo/isaac-workspace/projects/autoresearch"

$identity = (& ssh @sshOptions "leo@gx10-ddb2.local" "whoami").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}

$raw = (& ssh @sshOptions "leo@gx10-ddb2.local" `
    "test -f '$remoteRoot/current_status.json' && cat '$remoteRoot/current_status.json' || printf '{}'") -join "`n"
if ($LASTEXITCODE -ne 0) {
    throw "Could not read robot autoresearch status."
}
$status = $raw | ConvertFrom-Json
if (-not $status.status) {
    Write-Host "Robot autoresearch has no recorded run."
    exit 0
}

Write-Host "Status:     $($status.status)"
Write-Host "Phase:      $($status.phase)"
Write-Host "Robot:      $($status.robot_id)"
Write-Host "Run:        $($status.run_id)"
Write-Host "Cycle:      $($status.cycle)"
Write-Host "Checkpoint: $($status.checkpoint)"
if ($null -ne $status.best_score) {
    Write-Host "Best score: $($status.best_score)"
}
if ($null -ne $status.training_seconds_completed) {
    $completedMinutes = [math]::Round(
        [double]$status.training_seconds_completed / 60.0,
        1
    )
    Write-Host "PPO time:   $completedMinutes / $($status.training_budget_minutes) minutes"
}
if ($status.best_checkpoint_archive) {
    Write-Host "Best copy:  $($status.best_checkpoint_archive)"
}
if ($status.latest_advisory) {
    Write-Host "Advisory:   $($status.latest_advisory)"
}
if ($status.error) {
    Write-Host "Message:    $($status.error)"
}
if ($status.codex_request) {
    Write-Host "Codex file: $($status.codex_request)"
}
Write-Host "Data:       $remoteRoot/runs/$($status.robot_id)/$($status.run_id)"
