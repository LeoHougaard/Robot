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
$target = "leo@gx10-ddb2.local"
$remoteRoot = "/home/leo/isaac-workspace/projects/autoresearch"

$identity = (& ssh @sshOptions $target "whoami").Trim()
if ($LASTEXITCODE -ne 0 -or $identity -ne "leo") {
    throw "Refusing to continue because the remote identity is not exactly leo."
}

$stopCommand = @"
pid_file='$remoteRoot/controller.pid'
if [[ ! -f "`$pid_file" ]]; then
  printf 'Robot autoresearch is not running.\n'
  exit 0
fi
pid=`$(cat "`$pid_file")
if ! [[ "`$pid" =~ ^[0-9]+$ ]] || ! kill -0 "`$pid" 2>/dev/null; then
  printf 'Robot autoresearch is already stopped.\n'
  exit 0
fi
cmd=`$(tr '\0' ' ' <"/proc/`$pid/cmdline")
[[ "`$cmd" == *'/autoresearch/bin/robot_autoresearch.py'* ]] || {
  printf 'PID file does not identify the autoresearch supervisor.\n' >&2
  exit 2
}
kill -TERM "`$pid"
for _ in `$(seq 1 45); do
  kill -0 "`$pid" 2>/dev/null || {
    printf 'Robot autoresearch stopped. Checkpoints, videos, and logs were preserved.\n'
    exit 0
  }
  sleep 1
done
printf 'Supervisor is still stopping; it was not force-killed.\n' >&2
exit 3
"@
& ssh @sshOptions $target $stopCommand
if ($LASTEXITCODE -ne 0) {
    throw "Could not stop robot autoresearch cleanly."
}
