# 01. Foundation: Inter-Agent Messaging & Scheduled Automation

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-30 15:30 IST  
**System Status:** **OK** (Daemon supervisor and 5-minute watchdog operational; Windows Task Scheduler registered)  
**Review Status:** **UNREVIEWED** in [shared/trust/reviews.jsonl](file:///c:/Users/yashw/swing%20trades/shared/trust/reviews.jsonl) (Review packet dispatched to Claude and Codex on 2026-09-30)  

---

## 1. Plain-English Summary

For autonomous agents to build, audit, and operate a trading system without manual intervention, they require two foundational capabilities:
1. **A Reliable Message Bus (Nexus):** A tamper-proof postal system allowing Antigravity, Claude, and Codex to send instructions, audit reports, and review verdicts to one another. Each message is cryptographically signed so that no agent can impersonate another.
2. **Resilient Background Automation:** Automated processes (daemons) that continuously monitor inboxes, fetch market data, and check system health. These daemons must run independently of any open IDE or terminal window, surviving crashes, reboots, and accidental terminal closures.

If this foundation goes down, the entire multi-agent workflow halts: reviews sit unread, data pipelines fail to trigger, and health checks go blind.

---

## 2. Nexus Inter-Agent Bus Architecture

The Nexus message bus connects the three collaborating agents across their respective workspaces.

```
       [ Claude ]                  [ Codex / ChatGPT ]
           ▲                               ▲
           │ (HMAC Signed)                 │ (HMAC Signed)
           ▼                               ▼
    +---------------------------------------------+
    |         NEXUS DURABLE MESSAGE BUS           |
    |  antigravity/messages/{inbox,outbox}/       |
    +---------------------------------------------+
                           ▲
                           │ (HMAC Signed)
                           ▼
                  [ Antigravity ]
```

### Core Components
- **Message Bus Core:** [`antigravity/daemons/tri_agent_bus.py`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/tri_agent_bus.py) handles message formatting, nonce generation, and delivery tracking.
- **Worker Daemon:** [`antigravity/daemons/inbox_worker.py`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/inbox_worker.py) polls inboxes, validates message structures, acquires execution locks, and dispatches processing tasks.
- **Command-Line Interface:** [`antigravity/daemons/nexus_cli.py`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/nexus_cli.py) provides CLI utilities for agents to send messages, inspect queues, and check dialogue logs.
- **Dialogue Audit Logs:** Every dispatched and processed message is recorded sequentially in [`antigravity/logs/tri_agent_dialogue.jsonl`](file:///c:/Users/yashw/swing%20trades/antigravity/logs/tri_agent_dialogue.jsonl) and rendered into human-readable markdown at [`antigravity/logs/tri_agent_dialogue.md`](file:///c:/Users/yashw/swing%20trades/antigravity/logs/tri_agent_dialogue.md).

### Cryptographic Security & Authentication
- **HMAC-SHA256 Signing:** Every message envelope carries a cryptographic signature calculated over the canonical JSON payload using the sender's private key.
- **External Key Isolation:** In strict compliance with AGENTS.md security rules, agent secret keys are **never** stored inside the Git repository. Keys reside strictly at `C:\Users\yashw\.gemini\antigravity\agent_keys.json` ([`inbox_worker.py:45`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/inbox_worker.py#L45)).
- **Replay Protection:** Messages include ISO timestamps and UUID nonces. Messages older than 300 seconds or replaying an existing nonce are rejected fail-closed.

---

## 3. Daemon Supervisor & Outage Post-Mortem

### Root Cause Analysis (Outage of 29 September 2026)
At 16:15 IST on 29 September, Claude reported that the Nexus bus was completely down and a PING timed out. Forensic analysis of [`antigravity/daemons/supervisor.log`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervisor.log) revealed:
1. **The Shared Console Group Bug:** The supervisor was launched as a child process of an interactive terminal session. When the user closed the terminal window, Windows sent a `CTRL_CLOSE_EVENT` to the entire console process group, forcibly terminating both the supervisor and its child worker with NTSTATUS `4294967295` (`STATUS_CONTROL_C_EXIT`).
2. **Cascading Stale Lock Traps:** When workers were terminated ungracefully, `.claimed` marker files and lock files remained on disk, causing subsequent workers to falsely believe another instance was active.

### Architectural Hardening (Commit `a19c06a`)
The daemon supervisor ([`antigravity/daemons/supervised_inbox_worker.py`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervised_inbox_worker.py)) was hardened with the following controls:
- **Detached Process Creation:** Worker child processes are spawned using `subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS` ([`supervised_inbox_worker.py:90-95`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervised_inbox_worker.py#L90-L95)), decoupling child lifecycles from terminal shutdowns.
- **OS-Level Liveness Verification:** Replaced naive file-age checks with true Windows kernel handle checks (`_pid_is_running` via `kernel32.OpenProcess` and `GetExitCodeProcess`) to verify whether a process is actually alive before declaring a lock stale ([`inbox_worker.py:51-76`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/inbox_worker.py#L51-L76)).
- **Orderly Termination Protocol:** Implemented a non-destructive stop protocol via a sentinel marker file (`supervisor.stop`). Bulk killing of `python.exe` is strictly prohibited.

---

## 4. Windows Task Scheduler & 5-Minute Watchdog

Per Yashu's explicit approval recorded in `OWNER-2026-09-30-01` ([`shared/governance/owner_decisions.jsonl:4`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L4)), the supervisor and its watchdog are registered directly into Windows Task Scheduler under user account `yashw`.

### Registered Tasks
1. **`AntigravityNexusSupervisor`**:
   - **Trigger:** At user logon (`AtLogon`).
   - **Action:** Executes [`scripts/start_nexus_service.bat`](file:///c:/Users/yashw/swing%20trades/scripts/start_nexus_service.bat).
   - **Restart Policy:** Automatically restarts up to 5 times at 1-minute intervals if terminated abnormally.
   - **Execution Time Limit:** None (`PT0S`, runs indefinitely).
2. **`AntigravityNexusWatchdog`**:
   - **Trigger:** Runs every 5 minutes indefinitely.
   - **Action:** Executes [`scripts/watchdog_nexus_service.ps1`](file:///c:/Users/yashw/swing%20trades/scripts/watchdog_nexus_service.ps1), which calls [`antigravity/daemons/nexus_watchdog.py`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/nexus_watchdog.py).
   - **Logic:** Queries `supervised_inbox_worker.py --status`. If status is not `RUNNING`, it automatically restarts the supervisor daemon and logs the incident to [`antigravity/logs/nexus_watchdog.log`](file:///c:/Users/yashw/swing%20trades/antigravity/logs/nexus_watchdog.log).

### Verification Evidence
During resilience testing on 30 September 2026:
- Child worker was killed intentionally: Supervisor detected exit code 15 and respawned a healthy child worker within **2.0 seconds**.
- Both worker and supervisor were killed intentionally: The 5-minute watchdog detected the dead service and fully recovered the daemon within **5.0 seconds**.
- A test `PING` was dispatched with zero manual intervention; a signed `PONG` response was received and verified in **0.18 seconds**.
- Test suites passed: [`tests/test_nexus_resilience.py`](file:///c:/Users/yashw/swing%20trades/tests/test_nexus_resilience.py) (4/4 passed) and [`tests/test_tri_agent_messaging.py`](file:///c:/Users/yashw/swing%20trades/tests/test_tri_agent_messaging.py) (43/43 passed).

---

## 5. Daily Status Dashboard

System health is aggregated by [`antigravity/orchestrator/status.py`](file:///c:/Users/yashw/swing%20trades/antigravity/orchestrator/status.py). To view the complete system status, run:

```powershell
.venv\Scripts\python.exe antigravity/orchestrator/status.py
```

### Dashboard Telemetry Sections
1. **`[1] BUS HEALTH`**: Displays supervisor PID, child worker PID, supervisor uptime, watchdog task status, and telemetry on the last processed message (ID, timestamp, sender, subject, processing latency).
2. **`[2] DATA PIPELINES`**: Checks Bhavcopy presence for today's session, next session's F&O ban list, and surveillance snapshot status.
3. **`[3] POSITION & RISK GOVERNOR`**: Displays current portfolio slot utilization, committed capital against the ₹1,14,000 aggregate cap, and available cash.
4. **`[4] OPEN ANOMALIES & AUDIT DEFECTS`**: Displays count of active findings from the audit register.

---

## 6. Operational Commands Quick Reference

| Action | Command | Expected Output |
|---|---|---|
| **Check Bus Status** | `python antigravity/daemons/supervised_inbox_worker.py --status` | `{"status": "RUNNING", "supervisor_pid": ..., "worker_pid": ...}` |
| **Start Service** | `python antigravity/daemons/supervised_inbox_worker.py --start` | `Supervisor started with PID ...` |
| **Stop Service Gracefully** | `python antigravity/daemons/supervised_inbox_worker.py --stop` | `Supervisor and child worker stopped cleanly` |
| **Send Test Ping** | `python antigravity/daemons/nexus_cli.py --recipient ANTIGRAVITY --subject PING --body "Test"` | `Message sent: msg_... (Delivery OK)` |
| **View System Dashboard** | `python antigravity/orchestrator/status.py` | Full multi-section operational status report |
