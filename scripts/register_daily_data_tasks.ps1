# scripts/register_daily_data_tasks.ps1
# Registers Windows Task Scheduler tasks for Automated Daily NSE Data Ingestion
# Shifted from evening 18:45 to Afternoon 13:00 / 14:00 IST per Yashu's directive.

$ErrorActionPreference = "Stop"
$WorkspaceRoot = "C:\Users\yashw\swing trades"
$PythonExe = "$WorkspaceRoot\.venv\Scripts\python.exe"

# 1. Clean up old evening task if present
try {
    Unregister-ScheduledTask -TaskName "ARGUS_Daily_Evening_Collector" -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed old ARGUS_Daily_Evening_Collector (6:45 PM schedule)."
} catch {
    # Ignore if not present
}

Write-Host "[1/2] Registering ARGUS_Daily_Morning_Collector (Trigger: Daily at 07:30 IST)..."

$morningAction = New-ScheduledTaskAction -Execute $PythonExe -Argument "scripts/auto_daily_collector.py" -WorkingDirectory $WorkspaceRoot
$morningTrigger = New-ScheduledTaskTrigger -Daily -At "07:30"
$morningPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$morningSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "ARGUS_Daily_Morning_Collector" -Action $morningAction -Trigger $morningTrigger -Principal $morningPrincipal -Settings $morningSettings -Force

Write-Host "[2/2] Registering ARGUS_Daily_Afternoon_Collector (Triggers: Daily at 13:00 and 14:00 IST)..."

$afternoonAction = New-ScheduledTaskAction -Execute $PythonExe -Argument "scripts/auto_daily_collector.py" -WorkingDirectory $WorkspaceRoot
$afternoonTriggers = @(
    (New-ScheduledTaskTrigger -Daily -At "13:00"),
    (New-ScheduledTaskTrigger -Daily -At "14:00")
)
$afternoonPrincipal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive
$afternoonSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

Register-ScheduledTask -TaskName "ARGUS_Daily_Afternoon_Collector" -Action $afternoonAction -Trigger $afternoonTriggers -Principal $afternoonPrincipal -Settings $afternoonSettings -Force

Write-Host "\nVerifying Active Scheduled Tasks..."
Get-ScheduledTask -TaskName "ARGUS_Daily_Morning_Collector", "ARGUS_Daily_Afternoon_Collector" | Select-Object TaskName, State, @{Name="LogonUser"; Expression={$_.Principal.UserId}} | Format-Table -AutoSize
Write-Host "Daily Afternoon Data Collection tasks successfully registered for 1:00 PM and 2:00 PM IST."
