[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$target = "leo@gx10-ddb2.local"
$remoteRoot = "/home/leo/isaac-workspace/projects/autoresearch"
$localEscalations = Join-Path $PSScriptRoot "autoresearch\escalations"
$sshOptions = @(
    "-i", $keyPath,
    "-o", "IdentitiesOnly=yes",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=yes"
)

$identity = (& ssh @sshOptions $target "whoami").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}

$raw = (& ssh @sshOptions $target `
    "test -f '$remoteRoot/current_status.json' && cat '$remoteRoot/current_status.json' || printf '{}'") -join "`n"
$status = $raw | ConvertFrom-Json
if ($status.status -ne "needs_codex" -or -not $status.codex_request) {
    throw "The current autoresearch run does not have a pending Codex escalation."
}
if ($status.codex_request -notmatch '^/home/leo/isaac-workspace/projects/autoresearch/runs/[a-z0-9-]+/[0-9TZ]+/codex_request\.md$') {
    throw "The recorded Codex request path is outside the autoresearch run directory."
}

New-Item -ItemType Directory -Force -Path $localEscalations | Out-Null
$localRequest = Join-Path $localEscalations "$($status.run_id)-request.md"
$localResponse = Join-Path $localEscalations "$($status.run_id)-response.md"
& scp @sshOptions "${target}:$($status.codex_request)" $localRequest
if ($LASTEXITCODE -ne 0) {
    throw "Could not retrieve the Codex escalation request."
}

$codex = Get-Command codex -ErrorAction Stop
$prompt = @"
Review the robot-autoresearch escalation in this file:
$localRequest

Work read-only. Inspect this Robot Training repository as needed, diagnose the
failure from the evidence packet, and propose the smallest safe next action.
Do not edit files, start or stop containers, connect to the GB10, or expose
credentials. Clearly separate evidence, inference, and recommended changes.
"@

& $codex.Source exec `
    --sandbox read-only `
    --skip-git-repo-check `
    --output-last-message $localResponse `
    $prompt
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $localResponse -PathType Leaf)) {
    throw "Codex escalation analysis failed."
}

$remoteRun = Split-Path -Parent $status.codex_request
& scp @sshOptions $localResponse "${target}:$remoteRun/codex_response.md"
if ($LASTEXITCODE -ne 0) {
    throw "Codex responded, but its response could not be copied back to the GB10 run."
}

Write-Host "Codex escalation analysis complete."
Write-Host "Local response:  $localResponse"
Write-Host "Remote response: $remoteRun/codex_response.md"
