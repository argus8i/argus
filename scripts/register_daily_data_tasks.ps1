# scripts/register_daily_data_tasks.ps1
# Registers Windows Task Scheduler tasks for Automated Daily NSE Data Ingestion
# Authorized by Yashu for Project Swing Trades

$ErrorActionPreference = "Stop"
$WorkspaceRoot = "C:\Users\yashw\swing trades"
$PythonExe = "$WorkspaceRoot\.venv\Scripts\python.exe"

Write-Host "[1/3] Registering ARGUS_Daily_Morning_Collector (Trigger: Daily at 07:30 IST)..."

$morningAction = New-ScheduledTaskAction -Execute $PythonExe -Argument "scripts/auto_daily_collector.py" -WorkingDirectory $WorkspaceRoot
$morningTrigger = New-ScheduledTaskTrigger -Daily -At "07:30"
$morningPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$morningSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "ARGUS_Daily_Morning_Collector" -Action $morningAction -Trigger $morningTrigger -Principal $morningPrincipal -Settings $morningSettings -Force

Write-Host "[2/3] Registering ARGUS_Daily_Evening_Collector (Triggers: Daily at 18:45, 19:45, and 20:45 IST)..."

$eveningAction = New-ScheduledTaskAction -Execute $PythonExe -Argument "scripts/auto_daily_collector.py" -WorkingDirectory $WorkspaceRoot
$eveningTriggers = @(
    (New-ScheduledTaskTrigger -Daily -At "18:45"),
    (New-ScheduledTaskTrigger -Daily -At "19:45"),
    (New-ScheduledTaskTrigger -Daily -At "20:45")
)
$eveningPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$eveningSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "ARGUS_Daily_Evening_Collector" -Action $eveningAction -Trigger $eveningTriggers -Principal $eveningPrincipal -Settings $eveningSettings -Force

Write-Host "[3/3] Verifying Scheduled Tasks..."
Get-ScheduledTask -TaskName "ARGUS_Daily_Morning_Collector", "ARGUS_Daily_Evening_Collector" | Select-Object TaskName, State, @{Name="LogonUser"; Expression={$_.Principal.UserId}} | Format-Table -AutoSize
Write-Host "Daily Data Collection tasks successfully registered."
