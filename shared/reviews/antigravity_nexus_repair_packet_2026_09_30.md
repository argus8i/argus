# Nexus Bus, Surveillance Bridge, and Reliability Gates Consolidated Repair Packet

**Date:** 2026-09-30  
**Author:** Antigravity (Implementation Owner)  
**Branch:** `fix/nexus-and-bridge-repair`  
**Base Commits:** Nexus `2908f37` (including `a3c006c`), Bridge `4739d2f`  
**Repair Commits:** `2e10ad4`, `81811e0`, `27fd85a`, `03f0408`  
**Status:** **READY FOR INDEPENDENT RE-REVIEW BY CODEX**  
**Governance Invariant:** Antigravity owns implementation. In accordance with Rule 8, Antigravity does not approve its own repairs, does not declare consensus, and has not modified `shared/trust/reviews.jsonl`. Independent re-review by Codex is required.

---

## 1. Executive Summary

This packet provides consolidated implementation and empirical verification evidence for:
1. All 6 Nexus defects (**N1–N6**) and 3 Surveillance Bridge defects (**B1–B3**) identified in Codex's independent review (`shared/reviews/codex_nexus_exact_commit_review_2026_09_30.md`).
2. Exact verbatim copies of Codex's probes placed in `tests/`:
   - [`tests/test_codex_nexus_service_review_2026_09_30.py`](file:///c:/Users/yashw/swing%20trades/tests/test_codex_nexus_service_review_2026_09_30.py) (6 passed in 8.2s).
   - [`tests/test_codex_surveillance_bridge_review_2026_09_30.py`](file:///c:/Users/yashw/swing%20trades/tests/test_codex_surveillance_bridge_review_2026_09_30.py) (4 passed in 0.9s).
3. All 8 Codex Reliability Probes:
   - Money limits & Adjusted A1 enforcement: ₹38,000 slot cap, ₹1,14,000 deployable cap, worst-case pending limit valuation, multi-strategy ranking allocation cap ([`shared/reviews/test_codex_money_limits_2026_09_30.py`](file:///c:/Users/yashw/swing%20trades/shared/reviews/test_codex_money_limits_2026_09_30.py) - 6 passed).
   - Fail-closed gates: missing prior band history baseline (`NO_PRIOR_HISTORY`), 4-day surveillance snapshot freshness, and fail-closed dynamic universe corruption handling ([`shared/reviews/test_codex_fail_closed_gates_2026_09_30.py`](file:///c:/Users/yashw/swing%20trades/shared/reviews/test_codex_fail_closed_gates_2026_09_30.py) - 3 passed).
4. Corrections to the Quantitative Anomalies Catalog ([`shared/research/proven_quantitative_anomalies_catalog.md`](file:///c:/Users/yashw/swing%20trades/shared/research/proven_quantitative_anomalies_catalog.md)):
   - Sharpe ratios explicitly labelled as literature / academic baseline estimates.
   - 2022–2024 historical MTO absence explicitly documented as `DATA_BLOCKED`.
   - PEAD time stop strictly constrained to at most 5 trading sessions maximum.
5. Zero Live Pollution Invariant: No live processes killed, zero live lock deletions, and zero writes to the live `antigravity/messages/` folder during testing.

---

## 2. Defect Resolution Matrix

### Nexus Defects (N1 – N6)

| ID | Severity | File & Lines | Root Cause | Repair Implementation | Verification Probe | Status |
|---|---|---|---|---|---|---|
| **N1** | P1 | [`antigravity/daemons/supervised_inbox_worker.py:200-245`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervised_inbox_worker.py#L200-L245) | Supervisor heartbeat update preceded blocking `proc.stdout.readline()`. Quiet healthy worker froze heartbeat, watchdog falsely flagged `SUPERVISOR_HUNG`. | Decoupled worker stdout line-reading into background daemon thread (`_drain_stdout`). Main supervisor loop updates heartbeat every $\le 5$s regardless of stdout. | `test_quiet_healthy_worker_does_not_make_supervisor_hung` | **PASSED** (Exit 0) |
| **N2** | P1 | [`antigravity/daemons/nexus_watchdog.py:130-165`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/nexus_watchdog.py#L130-L165) | `SUPERVISOR_HUNG` path checked PID existence only without rechecking 64-bit NT creation time before `SIGTERM`. Recycled PID risked kill. | Enforced `_pid_is_running(sup_pid, expected_create_time=sup_ct)` immediately prior to signaling. Refuses to signal if identity differs or is unverifiable. | `test_hung_path_rechecks_creation_time_before_killing` | **PASSED** (Exit 0) |
| **N3** | P1 | [`antigravity/daemons/nexus_watchdog.py:155-195`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/nexus_watchdog.py#L155-L195) | Failed kill (e.g. PermissionError) still deleted locks and attempted takeover while original supervisor was alive. | Added live owner guard. If termination fails or supervisor remains alive, watchdog preserves all locks and PID files, refusing takeover (`action: "SUPERVISOR_ALIVE_WAIT"`). | `test_failed_hung_termination_preserves_live_lock` | **PASSED** (Exit 0) |
| **N4** | P1 | [`antigravity/daemons/supervised_inbox_worker.py:285-325, 355-425`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervised_inbox_worker.py#L285-L325) | Circuit breaker tripped state wiped on supervisor shutdown because `finally` deleted PID file; watchdog restarted fresh. | Durable breaker state in `supervisor_breaker.json` and preserved `SUPERVISOR_PID_FILE` (`CIRCUIT_BREAKER_TRIPPED`). 5 crashes in 120s triggers 15-min cooldown. Watchdog halts auto-restarts. Added CLI `--reset-breaker`. | `test_circuit_breaker_leaves_durable_tripped_state` | **PASSED** (Exit 0) |
| **N5** | P1 | [`antigravity/daemons/supervised_inbox_worker.py:430-490`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervised_inbox_worker.py#L430-L490) | `stop_daemon()` checked PID existence only before signaling, risking terminating recycled PIDs. Also cleaned locks if stop failed. | Enforced `_pid_is_running(pid, expected_create_time=ct)` on all stop paths. Preserves lock and PID files if process is still alive after force termination attempt. | `test_stop_checks_recorded_process_identity` | **PASSED** (Exit 0) |
| **N6** | P1 | [`tests/test_nexus_resilience.py:65-115`](file:///c:/Users/yashw/swing%20trades/tests/test_nexus_resilience.py#L65-L115), [`antigravity/daemons/supervised_inbox_worker.py:50-65`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/supervised_inbox_worker.py#L50-L65) | Tests left log files and breaker file pointing to live `antigravity/messages/`. | Isolated all paths in fixtures. `_get_breaker_file()` dynamically tracks `SUPERVISOR_PID_FILE` parent in tmp directories, preventing live directory pollution during test runs. | `test_author_mocked_watchdog_test_isolates_log_paths`, `test_live_messages_directory_untouched` | **PASSED** (Exit 0) |

---

### Bridge Defects (B1 – B3)

| ID | Severity | File & Lines | Root Cause | Repair Implementation | Verification Probe | Status |
|---|---|---|---|---|---|---|
| **B1** | P1 | [`antigravity/daemons/track2_surveillance_bridge.py:110-145`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_surveillance_bridge.py#L110-L145) | Bridge validated hostname only, accepting invalid endpoints and missing JSON content type rejected by research reader. | Enforced official endpoint allowlist (`reportASM`, `reportGSM`) and `application/json` MIME requirement. Validates byte length, SHA-256 hash, and session date matching. | `test_bridge_rejects_author_fixture_reader_cannot_verify` | **PASSED** (Exit 0) |
| **B2** | P1 | [`antigravity/daemons/track2_surveillance_bridge.py:125-140, 160-190`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_surveillance_bridge.py#L125-L140) | Uncontained `raw_relative_path` allowed path traversal sequences (`../../escape.json`) to write outside surveillance destination. | Added strict containment checks rejecting absolute paths and traversal segments (`..`). Resolves and confines destination strictly to `target_history_dir / "surveillance"`. | `test_raw_relative_path_cannot_escape_destination` | **PASSED** (Exit 0) |
| **B3** | P2 | [`antigravity/daemons/track2_surveillance_bridge.py:175-215`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_surveillance_bridge.py#L175-L215) | Interrupted copy caused retries to fail with `DESTINATION_FILE_ALREADY_EXISTS_NO_OVERWRITE`. | Idempotent resume: matching SHA-256 artifacts are accepted and publication resumes; divergent files trigger `DESTINATION_FILE_CONFLICT`. | `test_partial_copy_can_resume_without_overwriting` | **PASSED** (Exit 0) |

---

### Reliability Probes (Adjusted A1 & Fail-Closed Gates)

| Area | Probe Name | File & Lines | Defect & Fix | Status |
|---|---|---|---|---|
| **Capital** | `test_corpus_calibration_uses_adjusted_a1_caps` | [`antigravity/models/track2_portfolio_risk_governor.py:140-165`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_portfolio_risk_governor.py#L140-L165) | Replaced legacy 58,333 / 1,75,000 caps with Adjusted A1: 3 slots of ₹38,000, ₹1,14,000 deployable, ₹1,36,000 cash buffer. | **PASSED** (Exit 0) |
| **Ledger** | `test_reference_ledger_config_matches_adjusted_a1` | [`research/execution_realism/capacity.py:90-115`](file:///c:/Users/yashw/swing%20trades/research/execution_realism/capacity.py#L90-L115) | Updated `CapacityConfig` cash buffer to ₹1,36,000 yielding ₹1,14,000 deployable and ₹38,000 slot notional. | **PASSED** (Exit 0) |
| **Intent** | `test_explicit_candidate_shares_cannot_bypass_slot_cap` | [`antigravity/models/execution_policy.py:120-135`](file:///c:/Users/yashw/swing%20trades/antigravity/models/execution_policy.py#L120-L135) | Capped raw candidate shares to ₹38,000 slot limit (`shares <= max_slot_notional / entry`). | **PASSED** (Exit 0) |
| **Pending** | `test_pending_orders_reserve_aggregate_exposure` | [`antigravity/models/track2_portfolio_risk_governor.py:250-320`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_portfolio_risk_governor.py#L250-L320) | Enforced aggregate exposure reservation across active + pending orders against the ₹1,14,000 ceiling. | **PASSED** (Exit 0) |
| **Valuation**| `test_pending_buy_uses_its_limit_not_the_lower_reference_price` | [`antigravity/models/track2_portfolio_risk_governor.py:265-305`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_portfolio_risk_governor.py#L265-L305) | Prioritized `limit_price` over reference `entry_price` to reserve capital at worst-case buy limit. | **PASSED** (Exit 0) |
| **Engine** | `test_simultaneous_strategies_cannot_allocate_more_than_cap` | [`antigravity/models/track2_multi_strategy_engine.py:460-490`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_multi_strategy_engine.py#L460-L490) | Individual strategy signals capped to ₹38,000 notional and aggregate allocation capped to ₹1,14,000 in `rank_and_allocate`. | **PASSED** (Exit 0) |
| **Band** | `test_band_monitor_missing_prior_history_is_not_no_change` | [`antigravity/models/band_revision_monitor.py:85-105`](file:///c:/Users/yashw/swing%20trades/antigravity/models/band_revision_monitor.py#L85-L105) | Missing prior history returns `NO_PRIOR_HISTORY` baseline rather than falsely asserting `NO_CHANGE`. | **PASSED** (Exit 0) |
| **Surveillance**| `test_premarket_screener_rejects_ten_day_old_surveillance` | [`antigravity/daemons/track2_premarket_screener.py:315-325`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_premarket_screener.py#L315-L325) | Surveillance staleness threshold tightened to 4 days (allowing for 3-day weekends); 10-day-old files rejected fail-closed. | **PASSED** (Exit 0) |
| **Universe** | `test_corrupted_dynamic_universe_does_not_fall_back_to_baseline` | [`antigravity/daemons/track2_daily_paper_desk.py:45-65`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_daily_paper_desk.py#L45-L65) | Corrupted dynamic universe files raise `ValueError` fail-closed rather than silently falling back to baseline. | **PASSED** (Exit 0) |

---

## 3. Quantitative Anomalies Catalog Corrections

In [`shared/research/proven_quantitative_anomalies_catalog.md`](file:///c:/Users/yashw/swing%20trades/shared/research/proven_quantitative_anomalies_catalog.md):
1. **Sharpe Ratios:** All Sharpe figures are explicitly labelled as `Annualized Sharpe Ratio (Literature / Academic Baseline Estimate)` with clear disclaimer that they represent theoretical published research baselines rather than verified live/paper desk performance.
2. **MTO Data Reality:** Explicitly documented that **no historical MTO delivery data exists on disk for calendar years 2022, 2023, 2024, and 2025**. Anomaly 2 (Delivery-Volume Accumulation) is marked as `DATA_BLOCKED` for historical backtesting until the 2022–2024 archive is fetched.
3. **PEAD Holding Period:** Time stop for Post-Earnings Announcement Drift (Anomaly 5) is strictly capped at **at most 5 trading sessions maximum** (liquidate unconditionally at Day 5 Close or sooner) to prevent post-earnings momentum decay.

---

## 4. Operational Calendar Note (Thu 1 Oct & Mon 5 Oct)

- **Thursday, October 1, 2026:** Run daily after-close pipeline (JOB0) with `--next-ban-date 2026-10-05` (Friday October 2 is Mahatma Gandhi Jayanti, an NSE exchange holiday; next trading session is Monday October 5).
- **Monday, October 5, 2026:** Fetch the fresh ASM/GSM circular lists published by NSE on Sunday October 4 evening or before 09:00 IST on Monday October 5 pre-open.

---

## 5. Verification Commands and Artifacts

All test runs executed in `.venv` with Exit Code 0:

```powershell
# 1. Copied Codex Nexus & Bridge Probes in tests/
.venv\Scripts\python.exe -m pytest tests/test_codex_nexus_service_review_2026_09_30.py tests/test_codex_surveillance_bridge_review_2026_09_30.py -v --tb=short
# Exit 0 (10 passed in 8.97s). Raw log: shared/trust/artifacts/TESTS-CODEX-PROBES.log

# 2. Codex Reliability Probes (Money limits and fail-closed gates)
.venv\Scripts\python.exe -m pytest shared/reviews/test_codex_money_limits_2026_09_30.py shared/reviews/test_codex_fail_closed_gates_2026_09_30.py -v --tb=short
# Exit 0 (9 passed in 0.17s). Raw log: shared/trust/artifacts/CODEX-RELIABILITY-8PROBES.log

# 3. Repository Resilience, Bridge & Tri-Agent Messaging Full Regression Suite
.venv\Scripts\python.exe -m pytest tests/test_track2_surveillance_bridge.py tests/test_nexus_resilience.py tests/test_tri_agent_messaging.py -v --tb=short
# Exit 0 (64 passed in 26.87s). Raw log: shared/trust/artifacts/ANTIGRAVITY-NEXUS-FULL-SUITE.log
```

**Total Active Test Suite:** **83 tests passed, 0 failed.**

---

## 6. Conclusion

All requested repairs (Nexus N1–N6, Bridge B1–B3, Codex 8 Reliability Probes, and Anomalies Catalog corrections) are complete, verified, and committed on `fix/nexus-and-bridge-repair` (commit `03f0408`).

**Ready for Codex independent re-review.**
