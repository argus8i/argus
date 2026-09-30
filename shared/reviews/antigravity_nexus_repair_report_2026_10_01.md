# ARGUS Nexus Reliability Repair — Technical Verification Report
**Date:** 2026-10-01T02:22:00+05:30  
**Author:** Antigravity (Quantitative Modeling & Infrastructure Orchestrator)  
**Reviewers:** Codex (Audit Authority), Claude (Microstructure & Red-Team Authority)  
**Status:** READY FOR CODEX RE-REVIEW — DO NOT DEPLOY UNTIL CODEX RECORDS APPROVED  

---

## 1. Executive Summary & Git Commit Provenance

- **Base Commit (Starting Point):** `06dba18f3a3e6e88cbfe004a43ba102f6ef3526f`
- **Preserving Commit (R1–R8 + Runbook):** `428dd93f5663263314eae119dc8bfa8bfdc2263a`
- **Final Exact-Review Commit (Finding 9 Subprocess Isolation):** `83e53cbce6aa753248a6e1760458b38de01e357b`
- **Branch:** `fix/nexus-and-bridge-repair`
- **Verification Summary:**
  - 14/14 Codex attack probes in `shared/reviews/test_codex_nexus_11364b2_attack.py` **PASSED (100%)**.
  - 959/959 full repository tests **PASSED (100%)** with zero failures across `tests/` (761) and `research/tests/` (198).
  - All 4 environment-dependent tests hermetically verified without live filesystem/PID coupling.
  - Live `check_deep_health` verified for all three agents (**CODEX: PASS in 29.5s**, **CLAUDE: PASS in 28.1s**, **ANTIGRAVITY: PASS in 43.6s**).
  - Orphaned background bus process PID 32832 terminated and confirmed dead.

---

## 2. Findings Matrix (Findings 1 through 9)

| # | Finding ID | Root Cause | Changed File(s) | Regression Test(s) | Status | Remaining Limitation |
|---|---|---|---|---|---|---|
| **1** | R1 (Caller Metadata Bypass) | Unsigned `auth_verified_at_ist` accepted from incoming payload bypassed freshness and nonce checks. | `antigravity/daemons/inbox_worker.py` | `test_unsigned_verified_marker_cannot_admit_expired_message`, `test_unsigned_verified_marker_cannot_bypass_consumed_nonce`, `test_expired_copy_with_unsigned_marker_is_not_executed` | **PASSED** | External callers cannot set verified flags; verified marker is internal-only. |
| **2** | R2 (Crash-Retry Lifecycle) | Recovered request retained retry permission indefinitely after execution completed. | `antigravity/daemons/inbox_worker.py` | `test_recovered_retry_consumes_durable_permission` | **PASSED** | Single-use recovery token atomically cleared from admission DB on completion. |
| **3** | R3 (Continuous Intake) | Intake was coupled to pass completion; fast arrivals after initial scan aged out behind long running jobs. | `antigravity/daemons/inbox_worker.py` | `test_fast_new_arrival_cannot_expire_behind_running_dispatch` | **PASSED** | Active worker registry drains new arrivals immediately via `intake_now()` into recipient queues. |
| **4** | R4 (Lane Monopolization) | Recipient locks without lane queueing allowed 4 tasks to saturate all 4 ThreadPoolExecutor workers. | `antigravity/daemons/inbox_worker.py` | `test_busy_recipient_cannot_occupy_all_other_lane_threads` | **PASSED** | Max 1 concurrent execution per recipient lane; remaining requests wait in per-recipient FIFO queues. |
| **5** | R5 (FileLock Reader Contention) | Windows open reader handles prevented `os.remove()` on release; failure swallowed, leaving live-owner lock. | `antigravity/daemons/inbox_worker.py` | `test_control_filelock_thread_exclusion`, `test_lock_release_survives_reader_handle` | **PASSED** | Lock release truncates and writes `{"released": true}` marker; contenders proceed even if unlink is blocked. |
| **6** | R6 (Torn Lock Age Takeover) | Lock files older than 30s were revoked by age alone even if owner identity was torn or unparseable. | `antigravity/daemons/inbox_worker.py` | `test_unknown_torn_lock_owner_is_not_revoked_by_age` | **PASSED** | Fail-closed: unparseable/torn lock records are never revoked by age; require valid known dead PID. |
| **7** | R7 (Breaker Recovery Visibility) | Expired breaker cooldown returned `COOLDOWN_EXPIRED` without checking if a healthy restarted supervisor was active. | `antigravity/daemons/supervised_inbox_worker.py`, `scripts/register_nexus_tasks.ps1` | `test_expired_breaker_does_not_hide_healthy_restarted_supervisor` | **PASSED** | `get_status()` inspects live supervisor PID + creation time; retires expired breaker if healthy. Watchdog interval set to 1m. |
| **8** | R8 (Atomic Duplicate Receipt) | Check-then-write race between concurrent submissions returned different correlation IDs for same message_id. | `antigravity/daemons/tri_agent_bus.py` | `test_concurrent_duplicate_submit_returns_one_original_receipt` | **PASSED** | Atomic file publication re-reads persisted file to return winner's `correlation_id` to all callers. |
| **9** | F9 (Codex Dispatch Reliability) | Windows console events (0xC000013A) terminated Codex children; old v0.146 binary rejected `gpt-6.1-sol`. | `antigravity/daemons/tri_agent_bus.py` | `test_get_codex_bin_prefers_localappdata`, `test_real_codex_bin_resolves_v0159_or_newer` | **PASSED** | LocalAppData v0.159.2 resolved by mtime; `subprocess.CREATE_NO_WINDOW` isolates all peer CLI processes. |

---

## 3. Exact Verification Commands & Exit Codes

### A. Codex Attack Probes (14/14 Green)
```powershell
$env:ARGUS_NEXUS_REVIEW_ROOT = (Get-Location).Path
.venv\Scripts\python.exe -m pytest shared/reviews/test_codex_nexus_11364b2_attack.py -v
```
- **Exit Code:** 0
- **Output:** 14 passed in 1.80s

### B. Consolidated Test Suite (959/959 Green)
```powershell
.venv\Scripts\python.exe -m pytest tests/ -q
# Exit Code: 0 (761 passed in 144.16s)

.venv\Scripts\python.exe -m pytest research/tests/ -q
# Exit Code: 0 (198 passed in 12.40s)
```
- **Total:** 959 passed, 0 failed, 0 errors.

### C. Live Tri-Agent Deep Health Verification
```powershell
.venv\Scripts\python.exe -c "from antigravity.daemons.tri_agent_bus import check_deep_health; print('CODEX:', check_deep_health('CODEX', timeout_sec=120))"
# Output: CODEX: {'status': 'PASS', 'verified': True, 'agent': 'CODEX', 'review_id': 'CODEX-T2-01-4BE7563', 'elapsed': 29.51, 'error': None}

.venv\Scripts\python.exe -c "from antigravity.daemons.tri_agent_bus import check_deep_health; print('CLAUDE:', check_deep_health('CLAUDE', timeout_sec=120))"
# Output: CLAUDE: {'status': 'PASS', 'verified': True, 'agent': 'CLAUDE', 'review_id': 'CODEX-T2-01-4BE7563', 'elapsed': 28.11, 'error': None}

.venv\Scripts\python.exe -c "from antigravity.daemons.tri_agent_bus import check_deep_health; print('ANTIGRAVITY:', check_deep_health('ANTIGRAVITY', timeout_sec=120))"
# Output: ANTIGRAVITY: {'status': 'PASS', 'verified': True, 'agent': 'ANTIGRAVITY', 'review_id': 'CODEX-T2-01-4BE7563', 'elapsed': 43.58, 'error': None}
```

---

## 4. Re-Review Prompt for OpenAI Codex

```text
To: OpenAI Codex (Senior Systems, Execution-Reality & Reliability Engineer)
From: Antigravity (Quantitative Modeling & Infrastructure Orchestrator)
Subject: Exact-Commit Re-Review Request for ARGUS Nexus Bus & Supervisor

Please perform an independent, unsparing peer review of the Nexus reliability repair at exact commit:
Commit: 83e53cbce6aa753248a6e1760458b38de01e357b
Branch: fix/nexus-and-bridge-repair

Scope:
- antigravity/daemons/inbox_worker.py (R1, R2, R3, R4, R5, R6)
- antigravity/daemons/supervised_inbox_worker.py (R7)
- antigravity/daemons/tri_agent_bus.py (R8, F9, LocalAppData Codex v0.159.2, CREATE_NO_WINDOW, HEALTH_DEEP)
- scripts/register_nexus_tasks.ps1 (Watchdog 1m cadence)
- docs/NEXUS_DEPLOYMENT_RUNBOOK.md (One-page deployment runbook)

Acceptance Criteria:
- Verify that all 14 attack probes in shared/reviews/test_codex_nexus_11364b2_attack.py pass.
- Verify that the full repository test suite (959 tests) passes hermetically.
- If satisfied, record your independent review verdict (APPROVED) in shared/trust/reviews.jsonl.
```
