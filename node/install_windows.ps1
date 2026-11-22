# Registers the Plutus node as a scheduled task that starts at boot (run in an elevated PowerShell).
# Usage:  .\install_windows.ps1            (uses python from PATH and this folder)
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = (Get-Command python).Source
if (-not (Test-Path "$here\node.json")) { throw "Create node.json next to plutus_node.py first (see README.md)." }
$action = New-ScheduledTaskAction -Execute $python -Argument "`"$here\plutus_node.py`"" -WorkingDirectory $here
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
# Runs as SYSTEM so it starts with nobody logged in. Reading a network share then needs
# the share to allow this computer, or set inputs_dir to a local folder instead.
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName "Plutus node" -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName "Plutus node"
Write-Host "Plutus node installed and started. It appears on the Overview page within a minute."
