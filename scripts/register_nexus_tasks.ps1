# scripts/register_nexus_tasks.ps1
# Registers Windows Task Scheduler tasks for Antigravity Nexus Bus & Watchdog
# Authorized by Yashu for Project Swing Trades

$ErrorActionPreference = "Stop"
$WorkspaceRoot = "C:\Users\yashw\swing trades"
$PythonExe = "$WorkspaceRoot\.venv\Scripts\python.exe"

Write-Host "[1/3] Registering ARGUS_Nexus_Supervisor (Trigger: AtLogOn, RestartOnFailure)..."

$supAction = New-ScheduledTaskAction -Execute $PythonExe -Argument "-u antigravity/daemons/supervised_inbox_worker.py" -WorkingDirectory $WorkspaceRoot
$supTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$supPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$supSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "ARGUS_Nexus_Supervisor" -Action $supAction -Trigger $supTrigger -Principal $supPrincipal -Settings $supSettings -Force

Write-Host "[2/3] Registering ARGUS_Nexus_Watchdog (Trigger: Every 1 Minute)..."

$wdAction = New-ScheduledTaskAction -Execute $PythonExe -Argument "antigravity/daemons/nexus_watchdog.py --check" -WorkingDirectory $WorkspaceRoot
$wdTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).ToString("HH:mm") -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
$wdPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$wdSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName "ARGUS_Nexus_Watchdog" -Action $wdAction -Trigger $wdTrigger -Principal $wdPrincipal -Settings $wdSettings -Force

Write-Host "[3/3] Verifying Task Scheduler Registration..."
Get-ScheduledTask -TaskName "ARGUS_Nexus_Supervisor", "ARGUS_Nexus_Watchdog" | Select-Object TaskName, State, @{Name="LogonUser"; Expression={$_.Principal.UserId}} | Format-Table -AutoSize
Write-Host "Task Scheduler registration successfully completed."
