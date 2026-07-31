[CmdletBinding()]
param()

# Stopping the exact reusable Isaac Lab container interrupts a running policy
# cleanly enough for its launcher to mark the run, releases GPU/RAM, and
# preserves the imported robot, task code, logs, and checkpoints.
& (Join-Path $PSScriptRoot "Stop-IsaacLab.ps1")
