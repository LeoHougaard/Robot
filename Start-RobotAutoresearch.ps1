[CmdletBinding()]
param(
    [string]$Manifest = "autoresearch\robots\simple-dog.json",
    [string]$Queue = "",
    [switch]$DryRun,
    [switch]$DeployOnly
)

$ErrorActionPreference = "Stop"

$sshTarget = "leo@gx10-ddb2.local"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$remoteRoot = "/home/leo/isaac-workspace/projects"
$remoteAutoresearch = "$remoteRoot/autoresearch"
$remoteTraining = "$remoteRoot/training"
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
if ($Queue -and $PSBoundParameters.ContainsKey("Manifest")) {
    throw "Use either -Manifest or -Queue, not both."
}

$manifestFiles = @()
if ($Queue) {
    $localQueue = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot $Queue)).Path
    $queueConfig = Get-Content -Raw -LiteralPath $localQueue | ConvertFrom-Json
    if ($queueConfig.schema_version -ne 1 -or -not $queueConfig.manifests) {
        throw "Queue must use schema 1 and list at least one manifest filename."
    }
    foreach ($name in $queueConfig.manifests) {
        if ($name -notmatch '^[a-z0-9][a-z0-9-]{0,62}\.json$') {
            throw "Invalid manifest filename in queue: $name"
        }
        $manifestFiles += (Resolve-Path -LiteralPath (
            Join-Path $PSScriptRoot "autoresearch\robots\$name"
        )).Path
    }
    $remoteQueue = "$remoteAutoresearch/queues/$([IO.Path]::GetFileName($localQueue))"
    $launchSource = "--queue '$remoteQueue'"
} else {
    $localManifest = (Resolve-Path -LiteralPath (
        Join-Path $PSScriptRoot $Manifest
    )).Path
    $manifestFiles += $localManifest
    $remoteManifest = "$remoteAutoresearch/robots/$([IO.Path]::GetFileName($localManifest))"
    $launchSource = "--manifest '$remoteManifest'"
}

$identity = (& ssh @sshOptions $sshTarget "whoami").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}

$directories = @(
    $remoteAutoresearch,
    "$remoteAutoresearch/bin",
    "$remoteAutoresearch/prompts",
    "$remoteAutoresearch/queues",
    "$remoteAutoresearch/robots",
    "$remoteAutoresearch/runs",
    $remoteTraining,
    "$remoteTraining/assets",
    "$remoteTraining/simple_dog_task",
    "$remoteTraining/simple_dog_task/agents",
    "$remoteTraining/simple_dog_task_v2",
    "$remoteTraining/simple_dog_task_v2/agents"
)
& ssh @sshOptions $sshTarget ("install -d -m 0755 " + ($directories -join " "))
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare autoresearch directories."
}

$copies = @(
    @{ Local = "autoresearch\robot_autoresearch.py"; Remote = "$remoteAutoresearch/bin" },
    @{ Local = "autoresearch\test_robot_autoresearch.py"; Remote = $remoteAutoresearch },
    @{ Local = "autoresearch\prompts\evolution.md"; Remote = "$remoteAutoresearch/prompts" },
    @{ Local = "training\simple_dog_tuning.py"; Remote = $remoteTraining },
    @{ Local = "training\train_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = "training\run_simple_dog.sh"; Remote = $remoteTraining },
    @{ Local = "training\simple-dog-gb10.sh"; Remote = $remoteTraining },
    @{ Local = "training\inspect_simple_dog_run.py"; Remote = $remoteTraining },
    @{ Local = "training\prepare_rough_continuation_checkpoint.py"; Remote = $remoteTraining },
    @{ Local = "training\render_simple_dog_playback.sh"; Remote = $remoteTraining },
    @{ Local = "training\make_rollout_contact_sheet.py"; Remote = $remoteTraining },
    @{ Local = "training\validate_rollout_video.py"; Remote = $remoteTraining },
    @{ Local = "training\play_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = "training\ensure_simple_dog_meshes.sh"; Remote = $remoteTraining },
    @{ Local = "training\convert_onshape_gltf_to_usd.py"; Remote = $remoteTraining },
    @{ Local = "training\simple_dog_task\__init__.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = "training\simple_dog_task\simple_dog_env.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = "training\simple_dog_task\simple_dog_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = "training\simple_dog_task\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = "training\simple_dog_task\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = "training\simple_dog_task\agents\rl_games_rough_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = "training\simple_dog_task_v2\__init__.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = "training\simple_dog_task_v2\simple_dog_v2_env.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = "training\simple_dog_task_v2\simple_dog_v2_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = "training\simple_dog_task_v2\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_v2/agents" },
    @{ Local = "training\simple_dog_task_v2\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_v2/agents" },
    @{ Local = "training\assets\simple_dog_training.usda"; Remote = "$remoteTraining/assets" }
)
foreach ($manifestFile in $manifestFiles) {
    $copies += @{ Local = $manifestFile; Remote = "$remoteAutoresearch/robots" }
}
if ($Queue) {
    $copies += @{ Local = $localQueue; Remote = "$remoteAutoresearch/queues" }
}

foreach ($copy in $copies) {
    $local = if ([IO.Path]::IsPathRooted($copy.Local)) {
        $copy.Local
    } else {
        Join-Path $PSScriptRoot $copy.Local
    }
    if (-not (Test-Path -LiteralPath $local -PathType Leaf)) {
        throw "Required autoresearch file is missing: $local"
    }
    & scp @sshOptions $local "${sshTarget}:$($copy.Remote)/"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not deploy: $local"
    }
}

$executables = @(
    "$remoteAutoresearch/bin/robot_autoresearch.py",
    "$remoteTraining/run_simple_dog.sh",
    "$remoteTraining/simple-dog-gb10.sh",
    "$remoteTraining/render_simple_dog_playback.sh",
    "$remoteTraining/ensure_simple_dog_meshes.sh"
)
& ssh @sshOptions $sshTarget (
    "sed -i 's/\r$//' " + ($executables -join " ") +
    " && chmod 0755 " + ($executables -join " ")
)
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare deployed autoresearch executables."
}

Write-Host "Robot autoresearch files deployed."
Write-Host "Persistent data: $remoteAutoresearch"

if ($DeployOnly) {
    exit 0
}

$dryArg = if ($DryRun) { " --dry-run" } else { "" }
$launch = @"
test ! -f '$remoteAutoresearch/controller.pid' ||
  ! kill -0 `$(cat '$remoteAutoresearch/controller.pid') 2>/dev/null ||
  { printf 'An autoresearch supervisor is already active.\n' >&2; exit 4; }
nohup python3 '$remoteAutoresearch/bin/robot_autoresearch.py' \
  $launchSource$dryArg \
  >'$remoteAutoresearch/supervisor.log' 2>&1 </dev/null &
printf '%s\n' `$! >'$remoteAutoresearch/controller.pid'
printf '%s\n' `$!
"@
$remotePid = (& ssh @sshOptions $sshTarget $launch | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or $remotePid -notmatch '^\d+$') {
    throw "The remote autoresearch supervisor did not start."
}

Start-Sleep -Seconds 2
Write-Host "Robot autoresearch supervisor started (PID $remotePid)."
& (Join-Path $PSScriptRoot "Get-RobotAutoresearchStatus.ps1")
