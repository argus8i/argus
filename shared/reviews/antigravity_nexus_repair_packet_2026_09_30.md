# Nexus Bus and Surveillance Bridge Repair Packet

**Date:** 2026-09-30  
**Author:** Antigravity (Implementation Owner)  
**Branch:** `fix/nexus-and-bridge-repair`  
**Base Commits:** Nexus `2908f37` (including `a3c006c`), Bridge `4739d2f`  
**Repair Commits:** `2e10ad4`, `81811e0`  
**Status:** **READY FOR INDEPENDENT RE-REVIEW BY CODEX**  
**Governance Invariant:** Antigravity owns implementation. In accordance with Rule 8, Antigravity does not approve its own repairs, does not declare consensus, and has not modified `shared/trust/reviews.jsonl`. Independent re-review by Codex is required.

---

## 1. Executive Summary

This packet provides comprehensive implementation and verification evidence for all 6 Nexus defects (**N1–N6**) and all 3 Surveillance Bridge defects (**B1–B3**) identified in Codex's independent review (`shared/reviews/codex_nexus_exact_commit_review_2026_09_30.md`).

Every defect has been addressed test-first:
- **11 / 11 Codex review probes PASS** (Exit Code 0).
- **64 / 64 Repository regression tests PASS** (Exit Code 0).
- **Combined total: 75 tests passing, 0 failing.**
- Complete raw test logs and exit codes are recorded in `shared/trust/artifacts/`.
- No live processes were terminated, no live locks removed, and zero files in the live `antigravity/messages/` operational directory were modified or polluted during test execution.

---

## 2. Defect Resolution Matrix

### Nexus Defects (N1 – N6)

| ID | Severity | File & Lines | Root Cause | Repair Implementation | Verification Probe / Test | Outcome |
|---|---|---|---|---|---|---|
| **N1** | P1 | [`antigravity/daemons/supervised_inbox_worker.py:200-245`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervised_inbox_worker.py#L200-L245) | Supervisor heartbeat update preceded blocking `proc.stdout.readline()`. When a worker was healthy but quiet (e.g. during a long reasoning call), supervisor heartbeat froze and watchdog falsely flagged `SUPERVISOR_HUNG`. | Decoupled worker stdout line-reading into a dedicated background daemon thread (`_drain_stdout`). The supervisor main loop polls the worker process without blocking and issues regular `_update_heartbeat()` updates every $\le 5$s regardless of worker stdout activity. | `shared/reviews/test_codex_nexus_service_review_2026_09_30.py::test_quiet_healthy_worker_does_not_make_supervisor_hung` | **PASSED** (Exit 0) |
| **N2** | P1 | [`antigravity/daemons/nexus_watchdog.py:130-165`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/nexus_watchdog.py#L130-L165) | `SUPERVISOR_HUNG` handler checked PID existence via `os.kill(pid, 0)` without rechecking the 64-bit NT creation timestamp before issuing `SIGTERM`. If the original process died and its PID was recycled by the OS, an unrelated process could be terminated. | Added strict identity re-verification `_pid_is_running(sup_pid, expected_create_time=sup_ct)` immediately prior to signaling. If creation time is missing, mismatched, or unverifiable, the watchdog logs an explicit uncertainty warning and refuses to signal. | `shared/reviews/test_codex_nexus_service_review_2026_09_30.py::test_hung_path_rechecks_creation_time_before_killing` | **PASSED** (Exit 0) |
| **N3** | P1 | [`antigravity/daemons/nexus_watchdog.py:155-195`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/nexus_watchdog.py#L155-L195) | If termination raised `PermissionError` or failed to stop the hung supervisor, the watchdog proceeded to delete `supervisor.lock` and `.lock.lock`, attempting takeover while the original process was still alive. | Added live owner guard. If termination fails or `_pid_is_running(sup_pid, expected_create_time=sup_ct)` remains true after the timeout, the watchdog preserves all lock and PID files, refuses takeover, and returns `action: "SUPERVISOR_ALIVE_WAIT"`. | `shared/reviews/test_codex_nexus_service_review_2026_09_30.py::test_failed_hung_termination_preserves_live_lock` | **PASSED** (Exit 0) |
| **N4** | P1 | [`antigravity/daemons/supervised_inbox_worker.py:285-325, 355-425`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervised_inbox_worker.py#L285-L325) | Circuit breaker tripped state was wiped upon supervisor shutdown because `finally` deleted the PID file, and watchdog treated missing PID as `STOPPED`, clearing in-memory crash counts and looping restarts indefinitely. | Implemented durable circuit breaker state in `supervisor_breaker.json` and preserved `SUPERVISOR_PID_FILE` on breaker trip with status `CIRCUIT_BREAKER_TRIPPED`. Configured 5 crashes within 120s threshold with 900s (15 min) cooldown. Added `cooldown_remaining_sec` calculation to `get_status()`. Watchdog suspends automated task restarts when breaker is active. Added operator reset CLI (`--reset-breaker`). | `shared/reviews/test_codex_nexus_service_review_2026_09_30.py::test_circuit_breaker_leaves_durable_tripped_state` | **PASSED** (Exit 0) |
| **N5** | P1 | [`antigravity/daemons/supervised_inbox_worker.py:430-490`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervised_inbox_worker.py#L430-L490) | `stop_daemon()` checked only PID existence before sending `CTRL_BREAK_EVENT` / `SIGTERM`, risking termination of recycled PIDs. Also cleaned up locks even if supervisor process remained alive. | Enforced `_pid_is_running(pid, expected_create_time=ct)` for both supervisor and worker child processes during stop operations. Refuses termination if identity is unverified or mismatched. Preserves lock and PID files if supervisor is still alive after force termination attempt. | `shared/reviews/test_codex_nexus_service_review_2026_09_30.py::test_stop_checks_recorded_process_identity` | **PASSED** (Exit 0) |
| **N6** | P1 | [`tests/test_nexus_resilience.py:65-115`](file:///c:/Users/yashw/swing%20trades/tests/test_nexus_resilience.py#L65-L115), [`antigravity/daemons/supervised_inbox_worker.py:50-65`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervised_inbox_worker.py#L50-L65) | Author resilience tests redirected inboxes and PIDs but left `watchdog.log`, `supervisor.log`, and `supervisor_breaker.json` pointing to the live `antigravity/messages/` operational directory. | 1. Fully isolated all log and lock files in `isolate_nexus_filesystem` fixture.<br>2. Implemented `_get_breaker_file()` in `supervised_inbox_worker.py` which dynamically follows `SUPERVISOR_PID_FILE`'s parent directory when isolated to `tmp_path`, guaranteeing that external test harnesses (including Codex's review probes) never write to the live directory.<br>3. Added explicit assertion in `test_live_messages_directory_untouched`. | `shared/reviews/test_codex_nexus_service_review_2026_09_30.py::test_author_mocked_watchdog_test_isolates_log_paths`, `tests/test_nexus_resilience.py::test_live_messages_directory_untouched` | **PASSED** (Exit 0) |

---

### Bridge Defects (B1 – B3)

| ID | Severity | File & Lines | Root Cause | Repair Implementation | Verification Probe / Test | Outcome |
|---|---|---|---|---|---|---|
| **B1** | P1 | [`antigravity/daemons/track2_surveillance_bridge.py:110-145`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_surveillance_bridge.py#L110-L145) | Bridge validated only hostname (`www.nseindia.com`) without verifying endpoint identity or MIME content type. It accepted legacy fixtures with arbitrary endpoints (`/test/asm`) and missing content type that the exact research reader rejected. | Enforced official endpoint allowlist (`https://www.nseindia.com/api/reportASM` for ASM; `https://www.nseindia.com/api/reportGSM` for GSM). Required `content_type` starting with `application/json` or `text/json`. Validates byte length, SHA-256 hash, and session date matching. | `shared/reviews/test_codex_surveillance_bridge_review_2026_09_30.py::test_bridge_rejects_author_fixture_reader_cannot_verify`, `tests/test_track2_surveillance_bridge.py::test_bridge_rejects_invalid_legacy_fixture` | **PASSED** (Exit 0) |
| **B2** | P1 | [`antigravity/daemons/track2_surveillance_bridge.py:125-140, 160-190`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_surveillance_bridge.py#L125-L140) | Uncontained `raw_relative_path` allowed path traversal sequences (e.g. `../../escape.json`) to write files outside the intended research surveillance history directory. | Added strict containment checks rejecting absolute paths and traversal segments (`..`). Resolves both source and destination paths and enforces that the destination lies strictly within `target_history_dir / "surveillance"`. Rejects any escape attempt with `DESTINATION_TRAVERSAL_DETECTED`. | `shared/reviews/test_codex_surveillance_bridge_review_2026_09_30.py::test_raw_relative_path_cannot_escape_destination` | **PASSED** (Exit 0) |
| **B3** | P2 | [`antigravity/daemons/track2_surveillance_bridge.py:175-215`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_surveillance_bridge.py#L175-L215) | Interrupted publication left partially copied files that caused all subsequent retries to fail with `DESTINATION_FILE_ALREADY_EXISTS_NO_OVERWRITE`, preventing recovery without manual file deletion. | Implemented validated idempotent copy: if a destination file already exists, the bridge computes its SHA-256 hash. If the hash matches the source artifact, the file is accepted and publication resumes cleanly. If the hash differs, the bridge fails closed with `DESTINATION_FILE_CONFLICT` to protect evidence from being overwritten. | `shared/reviews/test_codex_surveillance_bridge_review_2026_09_30.py::test_partial_copy_can_resume_without_overwriting` | **PASSED** (Exit 0) |

---

## 3. Surveillance Bridge Operational Status & Policy

Per instructions and previous red-team audits:
1. **Bridge Operational State:** The bridge remains **RETIRED / OFF by default** in production operations.
2. **Architecture Rationale:** The research reader (`research/framework/market.py:MarketFiles`) reads surveillance snapshots directly from `shared/track2_liquid/surveillance/`. When run as an archive replication utility, the bridge provides verified byte-for-byte copies into `history/raw/nse/surveillance/`.
3. **Compatibility Commitment:** The bridge and reader roundtrip has been verified against the exact pinned research reader commit:
   - **Research Root:** `C:\Users\yashw\swing-trades-claude-004`
   - **Commit:** `158077991b70f83f82534e1ec785ba9f3d2bbc27`
   - **Probe:** `test_real_ingestor_bridge_reader_roundtrip` PASSED.

---

## 4. Verification Evidence & Raw Output Artifacts

All test runs were executed using the repository virtual environment (`.venv\Scripts\python.exe`) with exit codes and raw outputs captured.

### Test Commands & Exit Codes

```powershell
# 1. Codex Nexus Service Probes
$env:ARGUS_REVIEW_OPS_ROOT = "c:\Users\yashw\swing trades"
.venv\Scripts\python.exe -m pytest shared/reviews/test_codex_nexus_service_review_2026_09_30.py shared/reviews/test_codex_nexus_watchdog_2026_09_30.py -v --tb=short | Tee-Object -FilePath "shared\trust\artifacts\CODEX-NEXUS-REPAIR-probes.log"
# Exit Code: 0 (7 passed in 8.21s)

# 2. Codex Surveillance Bridge Probes (pinned against reader at 1580779)
$env:ARGUS_REVIEW_OPS_ROOT = "c:\Users\yashw\swing trades"
$env:ARGUS_REVIEW_RESEARCH_ROOT = "C:\Users\yashw\swing-trades-claude-004"
.venv\Scripts\python.exe -m pytest shared/reviews/test_codex_surveillance_bridge_review_2026_09_30.py -v --tb=short | Tee-Object -FilePath "shared\trust\artifacts\CODEX-BRIDGE-REPAIR-probes.log"
# Exit Code: 0 (4 passed in 0.97s)

# 3. Repository Resilience, Bridge & Tri-Agent Messaging Full Regression Suite
.venv\Scripts\python.exe -m pytest tests/test_track2_surveillance_bridge.py tests/test_nexus_resilience.py tests/test_tri_agent_messaging.py -v --tb=short | Tee-Object -FilePath "shared\trust\artifacts\ANTIGRAVITY-NEXUS-FULL-SUITE.log"
# Exit Code: 0 (64 passed in 26.87s)
```

### Log Artifact References
- [`shared/trust/artifacts/CODEX-NEXUS-REPAIR-probes.log`](file:///c:/Users/yashw/swing%20trades/shared/trust/artifacts/CODEX-NEXUS-REPAIR-probes.log)
- [`shared/trust/artifacts/CODEX-BRIDGE-REPAIR-probes.log`](file:///c:/Users/yashw/swing%20trades/shared/trust/artifacts/CODEX-BRIDGE-REPAIR-probes.log)
- [`shared/trust/artifacts/ANTIGRAVITY-NEXUS-FULL-SUITE.log`](file:///c:/Users/yashw/swing%20trades/shared/trust/artifacts/ANTIGRAVITY-NEXUS-FULL-SUITE.log)

---

## 5. Live Service Health Status

Verification of running Windows Scheduled Task services (`ARGUS_Nexus_Supervisor`):
- Supervisor PID: `30392`
- Worker Child PID: `20948`
- Daemon Status: `RUNNING`
- Worker Liveness: `True`
- Continuous Uptime: Since 16:09:02 IST without unplanned restarts or lock contention.
- Operational Directory: Live `antigravity/messages/` contains zero lock collisions and zero breaker trip files.

---

## 6. Conclusion & Request for Independent Review

All 6 Nexus defects and 3 Bridge defects demonstrated by Codex have been completely repaired and verified across 75 test probes without regressing existing contracts.

The changes are committed on branch `fix/nexus-and-bridge-repair` (commit `81811e0`).

**Antigravity requests Codex to perform an independent re-review of commit `81811e0`.**
