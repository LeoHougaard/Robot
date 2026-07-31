[CmdletBinding()]
param(
    [ValidateRange(128, 16384)]
    [int]$NumEnvs = 512,

    [ValidateRange(1, 100000)]
    [int]$MaxIterations = 500,

    [ValidateSet("Flat", "Rough", "V2Core", "V2Robust", "V2Goal")]
    [string]$Terrain = "Flat",

    [string]$Checkpoint = "",

    [string]$TuningConfig = ""
)

$ErrorActionPreference = "Stop"

$sshTarget = "leo@gx10-ddb2.local"
$keyPath = Join-Path $env:LOCALAPPDATA "NVIDIA Corporation\Sync\config\nvsync.key"
$localTraining = Join-Path $PSScriptRoot "training"
$remoteTraining = "/home/leo/isaac-workspace/projects/training"
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
if (-not (Test-Path -LiteralPath $localTraining -PathType Container)) {
    throw "Local simple-dog training package was not found: $localTraining"
}
if ($Checkpoint -and $Checkpoint -notmatch '^/workspace/projects/training/logs/rl_games/(simple_dog_(rough_)?velocity_direct|simple_dog_v2_locomotion_direct)/[A-Za-z0-9_./-]+\.pth$') {
    throw "Checkpoint must be a .pth file below a supported simple-dog training log directory."
}
$isV2Terrain = $Terrain -in @("V2Core", "V2Robust", "V2Goal")
$isV2Checkpoint = $Checkpoint -match '^/workspace/projects/training/logs/rl_games/simple_dog_v2_locomotion_direct/'
if ($Checkpoint -and $isV2Terrain -ne $isV2Checkpoint) {
    throw "V1 and V2 checkpoints are not interchangeable because their policy observations differ."
}
if ($Terrain -in @("V2Robust", "V2Goal") -and -not $Checkpoint) {
    throw "$Terrain is a continuation stage and requires a passing V2 checkpoint."
}
if ($TuningConfig -and $TuningConfig -notmatch '^/workspace/projects/autoresearch/[A-Za-z0-9_./-]+\.json$') {
    throw "TuningConfig must be a JSON file below /workspace/projects/autoresearch."
}

$identity = (& ssh @sshOptions $sshTarget "whoami").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}

$activeOutput = & ssh @sshOptions $sshTarget `
    "docker top isaac-lab-gb10 -eo pid,args 2>/dev/null | grep -F '[t]rain_simple_dog.py' >/dev/null && printf active || true"
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the current Isaac Lab workload."
}
$active = ($activeOutput -join "").Trim()
if ($active) {
    Write-Host "Simple-dog training is already running; it was not restarted."
    & (Join-Path $PSScriptRoot "Get-SimpleDogTrainingStatus.ps1")
    exit $LASTEXITCODE
}

# This starts only the reusable, unprivileged Isaac Lab container. It refuses
# to compete with the streamed Isaac Sim workload.
& (Join-Path $PSScriptRoot "Start-IsaacLab.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "Could not start the Isaac Lab container."
}

$remoteDirectories = @(
    $remoteTraining,
    "$remoteTraining/assets",
    "$remoteTraining/diagnostics",
    "$remoteTraining/simple_dog_task",
    "$remoteTraining/simple_dog_task/agents",
    "$remoteTraining/simple_dog_task_v2",
    "$remoteTraining/simple_dog_task_v2/agents"
)
& ssh @sshOptions $sshTarget ("install -d -m 0755 " + ($remoteDirectories -join " "))
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare the remote training directories."
}

$copies = @(
    @{ Local = Join-Path $localTraining "train_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "play_simple_dog.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "inspect_simple_dog_run.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "prepare_rough_continuation_checkpoint.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "simple_dog_tuning.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "run_simple_dog.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "simple-dog-gb10.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "ensure_simple_dog_meshes.sh"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "convert_onshape_gltf_to_usd.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "validate_simple_dog_stability.py"; Remote = $remoteTraining },
    @{ Local = Join-Path $localTraining "assets\simple_dog_training.usda"; Remote = "$remoteTraining/assets" },
    @{ Local = Join-Path $localTraining "simple_dog_task\__init__.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = Join-Path $localTraining "simple_dog_task\simple_dog_env.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = Join-Path $localTraining "simple_dog_task\simple_dog_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task" },
    @{ Local = Join-Path $localTraining "simple_dog_task\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task\agents\rl_games_rough_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\__init__.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\simple_dog_v2_env.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\simple_dog_v2_env_cfg.py"; Remote = "$remoteTraining/simple_dog_task_v2" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\agents\__init__.py"; Remote = "$remoteTraining/simple_dog_task_v2/agents" },
    @{ Local = Join-Path $localTraining "simple_dog_task_v2\agents\rl_games_ppo_cfg.yaml"; Remote = "$remoteTraining/simple_dog_task_v2/agents" }
)
foreach ($copy in $copies) {
    if (-not (Test-Path -LiteralPath $copy.Local -PathType Leaf)) {
        throw "Required training file was not found: $($copy.Local)"
    }
    & scp @sshOptions $copy.Local "${sshTarget}:$($copy.Remote)/"
    if ($LASTEXITCODE -ne 0) {
        throw "Could not deploy: $($copy.Local)"
    }
}

& ssh @sshOptions $sshTarget "sed -i 's/\r$//' $remoteTraining/run_simple_dog.sh $remoteTraining/simple-dog-gb10.sh $remoteTraining/ensure_simple_dog_meshes.sh && chmod 0755 $remoteTraining/run_simple_dog.sh $remoteTraining/simple-dog-gb10.sh $remoteTraining/ensure_simple_dog_meshes.sh"
if ($LASTEXITCODE -ne 0) {
    throw "Could not prepare the remote training launcher."
}

& ssh @sshOptions $sshTarget "docker exec isaac-lab-gb10 bash /workspace/projects/training/ensure_simple_dog_meshes.sh"
if ($LASTEXITCODE -ne 0) {
    throw "The Onshape geometry could not be prepared for Isaac Lab."
}

$checkpointArg = if ($Checkpoint) { $Checkpoint } else { "''" }
$tuningArg = if ($TuningConfig) { $TuningConfig } else { "''" }
$runDirectory = (& ssh @sshOptions $sshTarget `
    "$remoteTraining/simple-dog-gb10.sh start $NumEnvs $MaxIterations $checkpointArg $tuningArg $($Terrain.ToLowerInvariant())" |
    Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0 -or -not $runDirectory) {
    throw "The detached training process did not create a run directory."
}

Write-Host "Simple-dog training started."
Write-Host "Environments: $NumEnvs"
Write-Host "Iterations:   $MaxIterations"
Write-Host "Terrain:      $Terrain"
if ($Checkpoint) {
    Write-Host "Checkpoint:   $Checkpoint"
}
if ($TuningConfig) {
    Write-Host "Tuning:       $TuningConfig"
}
Write-Host "Run data:     $runDirectory"
Write-Host "Status:       .\Get-SimpleDogTrainingStatus.ps1"
Write-Host "Stop/free GPU: .\Stop-SimpleDogTraining.ps1"
