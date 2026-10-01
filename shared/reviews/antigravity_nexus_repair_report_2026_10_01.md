# ARGUS Nexus Reliability Repair — Technical Verification Report
**Date:** 2026-10-01T12:08:00+05:30  
**Author:** Antigravity (Quantitative Modeling & Infrastructure Orchestrator)  
**Reviewers:** Codex (Audit Authority), Claude (Microstructure & Red-Team Authority)  
**Status:** **APPROVED BY OPENAI CODEX** (Review ID: `CODEX-NEXUS-67C2D12`, Ledger Record `fa171ffcdfde0dfc10d39b91fef0cc2736302f1f12756ff425fafbd2c1486d8b`)  

---

## 1. Executive Summary & Git Commit Provenance

- **Base Commit (Starting Point):** `06dba18f3a3e6e88cbfe004a43ba102f6ef3526f`
- **Initial Fix Commit (R1–R8 + Runbook):** `428dd93f5663263314eae119dc8bfa8bfdc2263a`
- **Iterative Review Repairs:**
  - `582c313f927ebf02d87916d2ad66affc7dfc4668` (claim preservation during exhaustion, unlink only after outbox write)
  - `2a7da7307d3d0000f1862afcf8f53bb981e6350f` (all 4 recovery paths serialized under correlation FileLock)
  - `8e42cdb05a87dbabe3d9adb09381d87c274d3e2a` (claim validation failure serialized under correlation FileLock, Probe 23 equality & HMAC)
- **Final Approved Commit:** `67c2d12a097e7bd15fb4980d6d3abaf495f6d219`
- **Branch:** `fix/nexus-and-bridge-repair`
- **Verification Summary:**
  - 136/136 Nexus test suite tests **PASSED (100%)** across 7 files in 41.02s (Exit code: 0).
  - 14/14 Codex attack probes in `shared/reviews/test_codex_nexus_11364b2_attack.py` **PASSED (100%)**.
  - 26/26 followup probes in `tests/test_codex_nexus_followup_probes.py` **PASSED (100%)** (Probes 20–25).
  - Complete reproduction artifact verified and committed in `shared/trust/artifacts/CODEX-NEXUS-FULL-SUITE-VERIFIED.log`.
  - Independent AST parsing of 14 Python files passed; read-only verification exit code 0.
  - Formal verdict: **APPROVED** by OpenAI Codex.

---

## 2. Findings Matrix (Findings 1 through 18)

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
| **10** | F10 (FileLock Successor Safety) | Contender B acquiring while A releases could see lock deleted by A's late cleanup. | `antigravity/daemons/inbox_worker.py` | `test_filelock_never_deletes_successor_active_lock` | **PASSED** | Owner A writes `{"released": true}` without unlinking; contender B acquires cleanly without race deletion. |
| **11** | F11 (Atomic Cross-Process Admission) | Duplicate submissions across processes could admit conflicting bodies or return divergent receipts. | `antigravity/daemons/tri_agent_bus.py`, `antigravity/daemons/inbox_worker.py` | `test_cross_process_duplicate_submission_atomic_receipt` | **PASSED** | Coordinated atomic SQLite binding of `(message_id, correlation_id, payload_hash)` across all lifecycle states (`QUEUED`, `CLAIMED`, `COMPLETED`, `DEAD`). |
| **12** | F12 (Recovery Arrival Freshness) | Legitimate crashed requests recovered >300s after arrival were rejected by timestamp expiration. | `antigravity/daemons/inbox_worker.py` | `test_recovered_request_admitted_even_if_timestamp_older_than_300s` | **PASSED** | Requests admitted on arrival and marked recovering bypass `delta_sec > val_sec` arrival expiry via durable admission store. |
| **13** | F13 (Claude Review Read-Only Tools) | Claude review invocations passed empty tools instead of explicit read-only tools. | `antigravity/daemons/tri_agent_bus.py` | `test_claude_route_enforces_read_only_tools` | **PASSED** | Passed `--tools Read,Grep,Glob --permission-mode dontAsk` when `chat_only=True`. |
| **14** | F14 (Exhaustion Without Key) | In orphan recovery exhaustion, if agent secret key was unavailable, claim envelope was deleted without recording durable failure or writing outbox response. | `antigravity/daemons/inbox_worker.py` | `tests/test_codex_nexus_followup_probes.py::test_exhaustion_without_agent_secret_key_preserves_claim` (Probe 21) | **PASSED** | Claim envelope preserved until valid HMAC signature is signed and outbox published. |
| **15** | F15 (Schema/Task Failure Outbox Persistence) | Outbox publication failure during schema or task error unlinked `.claimed` file prematurely, losing evidence. | `antigravity/daemons/inbox_worker.py` | `tests/test_codex_nexus_followup_probes.py::test_schema_or_task_failure_outbox_failure_reconciled_on_second_pass` (Probe 23) | **PASSED** | Claim unlinked strictly after outbox publication succeeds; second pass safely reconciles from durable SQLite. |
| **16** | F16 (Recovery Outbox Mutual Exclusion) | Dead-letter sweep published responses without acquiring correlation `FileLock`, risking overwriting concurrent live winning reply. | `antigravity/daemons/inbox_worker.py` | `tests/test_codex_nexus_followup_probes.py::test_dead_letter_sweep_filelock_prevents_overwriting_concurrent_publisher` (Probe 24) | **PASSED** | All 4 recovery paths (`COMPLETED`, `DEAD`, orphan exhaustion, independent sweep) acquire correlation `FileLock` and recheck `os.path.exists`. |
| **17** | F17 (Claim Validation Outbox Mutual Exclusion) | `claim_message` validation-failure outbox write didn't acquire correlation `FileLock`. | `antigravity/daemons/inbox_worker.py` | `tests/test_codex_nexus_followup_probes.py::test_claim_validation_failure_filelock_prevents_overwriting_winning_response` (Probe 25) | **PASSED** | `claim_message` wraps outbox publication in `FileLock` with inside-lock existence recheck and durable SQLite persistence. |
| **18** | F18 (Rule 8 v2 Invariant 3 Reproduction Artifact) | Test suite log lacked exact invocation command, working directory, timestamps, and process exit code. | `scripts/run_and_record_nexus_suite.py`, `shared/trust/artifacts/CODEX-NEXUS-FULL-SUITE-VERIFIED.log` | Reviewer reproduction validation | **PASSED** | Complete reproduction artifact generated with command, CWD, timestamps, and `EXIT CODE: 0`. |

---

## 3. Exact Verification Commands & Exit Codes

### A. Full Nexus Test Suite (136/136 Green in 41.02s)
```powershell
.venv\Scripts\python.exe scripts/run_and_record_nexus_suite.py
```
- **Exit Code:** 0
- **Elapsed:** 41.48s
- **Output:** 136 passed in 41.02s
- **Retained Log:** `shared/trust/artifacts/CODEX-NEXUS-FULL-SUITE-VERIFIED.log` (SHA-256: `16fbf6fee5f3d26c8e295172b50474709e59cbe0d98a4d14ab8ec8f25f1db073`)

### B. Codex Attack & Followup Probes (40/40 Green)
```powershell
.venv\Scripts\python.exe -m pytest shared/reviews/test_codex_nexus_11364b2_attack.py tests/test_codex_nexus_followup_probes.py -v
```
- **Exit Code:** 0
- **Output:** 40 passed (14 attack probes + 26 followup probes) in 4.12s

---

## 4. OpenAI Codex Independent Review Verdict

```text
**APPROVED** for commit 67c2d12a097e7bd15fb4980d6d3abaf495f6d219, scoped to the Nexus repair and the previously outstanding reproduction-artifact gate.

Verified:
- Its direct parent is 8e42cdb05a87dbabe3d9adb09381d87c274d3e2a.
- The parent contains the claim-validation FileLock and existence recheck, Probe 23 A–D durable-response equality checks, and A–C HMAC checks.
- The committed test artifact records the command, CWD, timestamps, 136 passing tests across seven files, and exit code 0.
- Independent read-only AST parsing passed for 14 committed Python files; the verification command exited 0. Scoped workspace files match the commit after line-ending normalization.
```

---

## 5. Trust Ledger Confirmation

Record appended to `shared/trust/reviews.jsonl`:
- **Review ID:** `CODEX-NEXUS-67C2D12`
- **Reviewed Commit:** `67c2d12a097e7bd15fb4980d6d3abaf495f6d219`
- **Parent Commit:** `8e42cdb05a87dbabe3d9adb09381d87c274d3e2a`
- **Tree SHA:** `10f34c63c7731bade5cc59d09bfe714e72c946d8`
- **Patch SHA256:** `4988ef5696b64d4476a939bdfeb6b4d66c73bc439cfa56b4cfaffc067b141adc`
- **Verdict:** `APPROVED`
- **Record SHA256:** `fa171ffcdfde0dfc10d39b91fef0cc2736302f1f12756ff425fafbd2c1486d8b`
- **Chain Status:** Verified valid across all 19 records.

