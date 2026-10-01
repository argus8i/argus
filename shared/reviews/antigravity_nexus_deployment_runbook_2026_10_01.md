# ARGUS Nexus Bus & Supervisor Deployment Runbook
**Date:** 2026-10-01  
**Reviewed Commit:** `67c2d12` (Branch: `fix/nexus-and-bridge-repair`)  
**Target:** Live Production Task Scheduler & Message Bus (`c:\Users\yashw\swing trades`)  

---

> [!CAUTION]
> **MANDATORY DEPLOYMENT GATE:**
> **DO NOT DEPLOY** until both of the following gates are satisfied:
> 1. Codex independently recorded `APPROVED` on commit `67c2d12` in `shared/trust/reviews.jsonl` (Review ID: `CODEX-NEXUS-67C2D12`).
> 2. Yashu gives explicit written authorization to deploy ("Yashu says go on 67c2d12").
> Real service tasks, PIDs, and live message state must remain untouched until authorized. Do not deploy anything until Yashu says go on `67c2d12`.

---

## 1. Pre-Deployment Verification

1. Confirm git status is clean on `main` following fast-forward merge of the approved commit:
   ```powershell
   git status
   git log -n 1 --oneline
   ```
2. Confirm the complete test suite passes in an isolated worktree with 0 failures:
   ```powershell
   .venv\Scripts\python.exe -m pytest tests/ research/tests/ -q
   ```

---

## 2. Step (a): Scheduled Task Restart & Code Loading

Reload the running Windows scheduled task so that the in-memory Python supervisor and worker processes load the newly reviewed commit:

```powershell
# 1. Inspect current task execution state
schtasks /Query /TN "ARGUS_Nexus_Supervisor" /V /FO LIST

# 2. Stop running supervisor task
schtasks /End /TN "ARGUS_Nexus_Supervisor"

# 3. Wait 3 seconds for clean exit and PID file release
Start-Sleep -Seconds 3

# 4. Start fresh supervisor with reviewed codebase
schtasks /Run /TN "ARGUS_Nexus_Supervisor"

# 5. Wait 5 seconds and verify new supervisor.pid was written
Start-Sleep -Seconds 5
Get-Content antigravity/messages/supervisor.pid
```
- **Acceptance Criterion:** `supervisor.pid` exists, contains a positive PID, and `Get-Process -Id <pid>` is active.

---

## 3. Step (b): Head-of-Line Blocking Verification (Lane Concurrency)

Prove that a slow request on the `CODEX` lane does not block an urgent `PING` to `ANTIGRAVITY`:

```powershell
# In a test terminal or automated probe:
& "c:\Users\yashw\swing trades\.venv\Scripts\python.exe" -c @"
import time, threading
from antigravity.daemons.tri_agent_bus import TriAgentBus

bus = TriAgentBus()

# 1. Enqueue slow task to CODEX
def send_slow():
    bus.send_request(recipient='CODEX', task_type='SLOW_PROBE', payload={'duration': 10}, timeout_seconds=15)

t = threading.Thread(target=send_slow)
t.start()
time.sleep(0.5)

# 2. Immediately send PING to ANTIGRAVITY
t0 = time.time()
resp = bus.send_request(recipient='ANTIGRAVITY', task_type='PING', payload={'test': 'hol_check'}, timeout_seconds=3)
elapsed = time.time() - t0

print(f'ANTIGRAVITY PING returned in {elapsed:.3f}s: status={resp.get(\"status\")}')
assert elapsed < 2.0, f'Head-of-line blocking detected: PING took {elapsed:.2f}s'
assert resp.get('status') in ('SUCCESS', 'COMPLETED', 'ACK')
t.join()
print('PASS: Head-of-line blocking resolved across distinct recipient lanes.')
"@
```
- **Acceptance Criterion:** `ANTIGRAVITY` PING completes in $<2.0\text{s}$ while `CODEX` lane is busy.

---

## 4. Step (c): Resilience & Failover Verification (Crash Recovery < 2 min)

Verify that supervisor self-heals worker crashes, and watchdog self-heals supervisor crashes:

```powershell
# 1. Read current supervisor and worker PIDs
$pidData = Get-Content antigravity/messages/supervisor.pid | ConvertFrom-Json
$supPid = $pidData.supervisor_pid
$workerPid = $pidData.worker_pid

Write-Host "Killing worker PID=$workerPid..."
Stop-Process -Id $workerPid -Force
Start-Sleep -Seconds 5

# Verify supervisor spawned a replacement worker
$newPidData = Get-Content antigravity/messages/supervisor.pid | ConvertFrom-Json
Write-Host "New worker PID=$($newPidData.worker_pid) (was $workerPid)"
assert ($newPidData.worker_pid -ne $workerPid)

# 2. Kill supervisor process
Write-Host "Killing supervisor PID=$supPid..."
Stop-Process -Id $supPid -Force

# 3. Wait for ARGUS_Nexus_Watchdog (runs every 1 min) to detect and relaunch
Write-Host "Waiting up to 120s for Watchdog recovery..."
$restarted = $false
for ($i = 0; $i -lt 24; $i++) {
    Start-Sleep -Seconds 5
    if (Test-Path antigravity/messages/supervisor.pid) {
        $recoveredData = Get-Content antigravity/messages/supervisor.pid | ConvertFrom-Json
        if ($recoveredData.supervisor_pid -ne $supPid) {
            $restarted = $true
            Write-Host "Supervisor successfully restored! New PID=$($recoveredData.supervisor_pid)"
            break
        }
    }
}
if (-not $restarted) { throw "FAIL: Watchdog did not restore supervisor within 2 minutes." }
```
- **Acceptance Criterion:** Full supervisor and worker stack restored and responsive within $\le 120\text{ seconds}$.

---

## 5. Step (d): 24-Hour Soak Invariant

Execute a 24-hour background soak sending a heartbeat PING every 10 minutes:

```powershell
# Run the soak probe in background or via Task Scheduler:
$soakDate = (Get-Date).ToString("yyyy_MM_dd")
$soakLog = "shared/trust/artifacts/nexus_soak_${soakDate}.log"

& "c:\Users\yashw\swing trades\.venv\Scripts\python.exe" -u -c @"
import time, datetime
from antigravity.daemons.tri_agent_bus import TriAgentBus

bus = TriAgentBus()
log_path = '$soakLog'

with open(log_path, 'a', encoding='utf-8') as f:
    f.write(f'=== Soak started at {datetime.datetime.now().isoformat()} ===\n')

for i in range(144): # 24 hours @ 10-minute intervals
    t0 = time.time()
    try:
        resp = bus.send_request(recipient='ANTIGRAVITY', task_type='PING', payload={'seq': i}, timeout_seconds=10)
        dt = time.time() - t0
        st = resp.get('status', 'UNKNOWN')
        entry = f'[{datetime.datetime.now().isoformat()}] seq={i} status={st} latency={dt:.3f}s\n'
    except Exception as exc:
        dt = time.time() - t0
        entry = f'[{datetime.datetime.now().isoformat()}] seq={i} status=ERROR exc={exc} latency={dt:.3f}s\n'
    with open(log_path, 'a', encoding='utf-8') as f:
        f.write(entry)
    time.sleep(600)
"@
```
- **Acceptance Criterion:** 144 consecutive successful PINGs recorded in `shared/trust/artifacts/nexus_soak_<date>.log` with zero timeouts, zero deadlocks, and stable memory.

---

## 6. Step (e): Post-Reboot Invariant

On Yashu's next machine restart / reboot:
1. Verify task status without manual intervention:
   ```powershell
   schtasks /Query /TN "ARGUS_Nexus_Supervisor" /V /FO LIST
   ```
2. Verify PID file presence:
   ```powershell
   Get-Content antigravity/messages/supervisor.pid
   ```
3. Execute single test ping:
   ```powershell
   & "c:\Users\yashw\swing trades\.venv\Scripts\python.exe" -c "from antigravity.daemons.tri_agent_bus import TriAgentBus; print(TriAgentBus().send_request('ANTIGRAVITY', 'PING', {'test': 'post_reboot'}, timeout_seconds=5))"
   ```
- **Acceptance Criterion:** Post-reboot auto-start verified with zero manual interventions.
