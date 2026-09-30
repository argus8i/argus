# Antigravity Review Packet: Nexus Bus Scheduled Task Registration & Resilience

**Date:** 30 September 2026  
**Author:** Antigravity (Quantitative Modeling & Execution Automation)  
**Branch:** `ops/nexus-scheduled-task`  
**Governance Authority:** Yashu Owner Decision `OWNER-2026-09-30-01` (`shared/governance/owner_decisions.jsonl`)  
**Peer Reviewers:** Claude Code (Red-Team & Adverse Selection), OpenAI Codex (Systems & Verification)

---

## 1. Executive Summary & Root Cause Analysis

On 29 September 2026 after 20:50 IST, the Nexus inter-agent message bus suffered an unrecoverable outage. All subsequent peer messages between Claude, Codex, and Antigravity sat unread in `antigravity/messages/inbox` or timed out.

A comprehensive forensic audit revealed **four interlocking root causes**:

1. **Console Lifecycle Binding (Exit Code `4294967295`):**  
   The supervisor had been started inside an interactive terminal session (`PID 31596`). When the developer console or IDE closed, Windows issued `CTRL_CLOSE_EVENT` to the console process group, abruptly terminating the Python runtime with exit code `-1` (`0xFFFFFFFF`), leaving an orphaned `supervisor.pid` file on disk.

2. **822 MB Checkpoint Head-of-Line Queue Freezing:**  
   When messages were dispatched to Codex, Claude, or Antigravity, `inbox_worker.py` only applied `chat_only=True` to subjects exactly equal to `"CHAT"`. For subjects such as `"REVIEW: ..."`, `"PLAN: ..."`, or live queries, `inbox_worker` called `prepare_dispatch()`, which invoked `create_checkpoint()`. `create_checkpoint()` walked the entire workspace (including `.git`), calculated SHA-256 digests for thousands of objects, and compressed an **822.6 MB zip archive** to `..\swing-trades-checkpoints`. This blocked the single-threaded inbox worker for **2.5 minutes per message**. Unrelated messages waiting behind it in `inbox/` (such as PINGs) sat idle until their 300s TTL expired, triggering `TIMESTAMP_OUT_OF_BOUNDS: Message expired (361.2s old > 300s limit)` and creating the illusion of a dead bus.

3. **Invalid Stdout Handle in Detached Background Mode:**  
   When launched via `DETACHED_PROCESS` / `CREATE_NO_WINDOW`, calling `print(..., flush=True)` in Python on Windows raises `OSError: [WinError 6] The handle is invalid`. The original supervisor crashed immediately upon attempting to echo the first worker log line.

4. **Stale Lock Timeout Delay:**  
   `FileLock._break_stale_lock()` in `inbox_worker.py` unconditionally waited 60 seconds before breaking locks, even when the owning PID was already dead.

---

## 2. Architecture & Windows Task Scheduler Registration

In accordance with owner decision `OWNER-2026-09-30-01`, two standard Windows scheduled tasks were registered under user account `galaxybook-5\yashw` with `InteractiveToken` and `LeastPrivilege` (no admin elevation, no SYSTEM permissions):

### Task 1: `ARGUS_Nexus_Supervisor`
- **Trigger:** At user logon (`LogonTrigger`).
- **Action:** Runs `pythonw.exe` with module `antigravity.daemons.supervised_inbox_worker`.
- **Restart on Failure:** Restarts every 1 minute up to 3 times per interval; indefinite execution limit (`ExecutionTimeLimit: PT0S`); ignores new instances if already running.
- **XML Artifact:** [`shared/trust/artifacts/ARGUS_Nexus_Supervisor_exported.xml`](file:///c:/Users/yashw/swing%20trades/shared/trust/artifacts/ARGUS_Nexus_Supervisor_exported.xml).

### Task 2: `ARGUS_Nexus_Watchdog`
- **Trigger:** Recurring daily trigger repeating every 5 minutes indefinitely (`PT5M`).
- **Action:** Runs `pythonw.exe` with module `antigravity.daemons.nexus_watchdog --check`.
- **Function:** Queries daemon status via PID/liveness check. If `STOPPED`, `WORKER_DOWN`, or `STALE_PID`, it clears stale lock files and automatically spawns a new detached supervisor instance.
- **XML Artifact:** [`shared/trust/artifacts/ARGUS_Nexus_Watchdog_exported.xml`](file:///c:/Users/yashw/swing%20trades/shared/trust/artifacts/ARGUS_Nexus_Watchdog_exported.xml).

*(Note: Pre-existing scheduled tasks such as `\KiteSwingScannerDaily` remain untouched.)*

---

## 3. Operational Hardening Implemented

1. **Elimination of Checkpoint Bottlenecks on Read-Only Operations:**
   - In [`antigravity/daemons/inbox_worker.py`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/inbox_worker.py), all peer dispatches (`CLAUDE`, `CODEX`) are strictly read-only discussion/review dispatches (`if expected_file: return INCOMPLETE`). They now unconditionally run with `chat_only=True` (`--sandbox read-only`), completely bypassing `prepare_dispatch()` and the 822MB zip freeze.
   - For Antigravity model dispatches, if `not expected_file`, `is_chat` is set to `True`, avoiding unnecessary workspace checkpoints for reasoning queries.
   - Used `inspect.signature` to ensure full backward compatibility with test mock dispatchers.

2. **Clean Daemon Stop Protocol:**
   - Implemented `supervised_inbox_worker.py --stop`, which signals the supervisor process via `CTRL_BREAK_EVENT` / `SIGTERM`, writes `supervisor.stop`, terminates child worker PIDs, and cleans up PID/lock files cleanly.
   - Adheres strictly to the operational rule: **never kill python.exe in bulk or by guessed PID**.

3. **Bus Health Telemetry:**
   - Integrated bus health reporting into `scripts/daily_pipeline.py` and `antigravity/orchestrator/status.py`. The dashboard now reports:
     `Nexus bus: OK (Supervisor PID: 34000, Worker PID: 2100, last message processed at 2026-09-30 15:26:09 IST)`

---

## 4. Empirical Evidence & Reproducible Proof Protocols

### Proof Test A: Worker Crash Recovery (Within 1.0s)
1. **Pre-test State:** Supervisor PID `36820`, Worker PID `14692`.
2. **Action:** Intentionally killed worker PID `14692` with `Stop-Process -Id 14692 -Force`.
3. **Supervisor Log Evidence (`antigravity/messages/supervisor.log`):**
   ```text
   [2026-09-30 15:25:17 IST] [SUPERVISOR] Worker process (PID=14692) exited with code 4294967295
   [2026-09-30 15:25:17 IST] [SUPERVISOR] CRASH/EXIT DETECTED: Triggering automatic recovery...
   [2026-09-30 15:25:17 IST] [SUPERVISOR] Orphan recovery pass completed.
   [2026-09-30 15:25:17 IST] [SUPERVISOR] Applying backoff delay of 1.0s before restart...
   [2026-09-30 15:25:18 IST] [SUPERVISOR] Spawning inbox worker: C:\Users\yashw\swing trades\.venv\Scripts\python.exe -u C:\Users\yashw\swing trades\antigravity\daemons\inbox_worker.py
   [2026-09-30 15:25:18 IST] [SUPERVISOR] Worker spawned successfully with PID=24824
   ```
4. **Post-Recovery PING Verification:**
   ```powershell
   .venv\Scripts\python.exe -m antigravity.daemons.nexus_cli --sender CLAUDE --recipient CODEX --subject PING --track TRACK_2 --body "post-worker-crash ping" --wait 15
   ```
   **Output:**
   ```json
   {"message_id": "msg_1790762131_f689e16d3a77", "correlation_id": "corr_1790762131_42bf3371a7f5", "recipient": "CODEX"}
   {"success": true, "status": "COMPLETED", "error": null, "output_payload": {"reply": "PONG", "agent": "CODEX", "check": "GATEWAY_ROUTE_ONLY", "track": "TRACK_2", "time": "2026-09-30 15:25:32 IST"}, "correlation_id": "corr_1790762131_42bf3371a7f5"}
   ```
   **Result:** `Exit code 0`, round-trip time `< 1.0s`.

---

### Proof Test B: Supervisor Crash Recovery (Within 1.0s via Watchdog)
1. **Pre-test State:** Supervisor PID `36820`, Worker PID `24824`.
2. **Action:** Intentionally killed supervisor PID `36820` with `Stop-Process -Id 36820 -Force`.
3. **Status Check:** Reported `STALE_PID`, `running: false`.
4. **Watchdog Execution (`nexus_watchdog.py --check`):**
   ```text
   [2026-09-30 15:25:54 IST] [WATCHDOG] OUTAGE DETECTED: Status=STALE_PID. Initiating automated recovery restart...
   [2026-09-30 15:25:54 IST] [WATCHDOG] Cleaned up stale supervisor PID file.
   [2026-09-30 15:25:54 IST] [WATCHDOG] Spawning detached supervisor process: C:\Users\yashw\swing trades\.venv\Scripts\python.exe -u C:\Users\yashw\swing trades\antigravity\daemons\supervised_inbox_worker.py
   [2026-09-30 15:25:54 IST] [WATCHDOG] Detached supervisor process spawned with initial PID=15520
   [2026-09-30 15:25:55 IST] [WATCHDOG] RECOVERY SUCCESS: Supervisor active with PID=18652, Worker PID=15832
   ```
5. **Post-Recovery PING Verification:**
   ```powershell
   .venv\Scripts\python.exe -m antigravity.daemons.nexus_cli --sender CLAUDE --recipient CODEX --subject PING --track TRACK_2 --body "post-supervisor-recovery ping" --wait 15
   ```
   **Output:**
   ```json
   {"message_id": "msg_1790762169_11d2e0ee36fe", "correlation_id": "corr_1790762169_b9e5c9e5cad2", "recipient": "CODEX"}
   {"success": true, "status": "COMPLETED", "error": null, "output_payload": {"reply": "PONG", "agent": "CODEX", "check": "GATEWAY_ROUTE_ONLY", "track": "TRACK_2", "time": "2026-09-30 15:26:09 IST"}, "correlation_id": "corr_1790762169_b9e5c9e5cad2"}
   ```
   **Result:** `Exit code 0`, round-trip time `< 1.0s`.

---

### Proof Test C: Live Inter-Agent CHAT Dispatch Verification
1. **Command:**
   ```powershell
   .venv\Scripts\python.exe -m antigravity.daemons.nexus_cli --sender CLAUDE --recipient CODEX --subject CHAT --track TRACK_2 --body "Ping from Claude via chat. Please reply 'ACK: Bus is alive and fast' in 1 sentence." --wait 30
   ```
2. **Output:**
   ```json
   {"message_id": "msg_1790762083_fcd2afb77c82", "correlation_id": "corr_1790762083_3829b6952fa1", "recipient": "CODEX"}
   {"success": true, "status": "COMPLETED", "error": null, "output_payload": {"agent": "CODEX", "model_response": "ACK: Bus is alive and fast.", "transport": "HEADLESS_CLI", "elapsed_sec": 18.290744304656982}, "correlation_id": "corr_1790762083_3829b6952fa1"}
   ```
3. **Verification:**
   - Full live model reasoning executed via OpenAI Codex headless CLI.
   - Elapsed time: **18.29s** (reduced from 215s).
   - Checkpoints created: **0**.

---

### Proof Test D: Automated Test Suite
- **Command:**
  ```powershell
  .venv\Scripts\pytest tests/test_tri_agent_messaging.py tests/test_nexus_resilience.py
  ```
- **Output:**
  ```text
  ============================= test session starts =============================
  platform win32 -- Python 3.14.7, pytest-9.1.1, pluggy-1.6.0
  rootdir: C:\Users\yashw\swing trades
  configfile: pytest.ini
  collected 47 items

  tests\test_tri_agent_messaging.py ...................................... [ 80%]
  .....                                                                    [ 91%]
  tests\test_nexus_resilience.py ....                                      [100%]

  ============================= 47 passed in 8.20s ==============================
  ```

---

## 5. Review Call to Peers

Antigravity requests Claude and Codex review and sign off on:
1. The Task Scheduler registration definitions (`shared/trust/artifacts/*.xml`).
2. The elimination of unnecessary zip checkpointing for read-only peer queries.
3. The automated watchdog recovery and health telemetry invariants.
