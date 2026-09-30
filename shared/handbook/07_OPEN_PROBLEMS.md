# 07. Unified Open Problems, Defect Ledger & Data Gaps

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-30 15:30 IST  
**System Status:** **BROKEN** (18 active engineering, review, and data problems requiring systematic remediation)  
**Defect Sources:** [38_findings_raw.txt](file:///c:/Users/yashw/swing%20trades/38_findings_raw.txt) | [shared/trust/reviews.jsonl](file:///c:/Users/yashw/swing%20trades/shared/trust/reviews.jsonl) | [shared/reviews/](file:///c:/Users/yashw/swing%20trades/shared/reviews/)  

---

## 1. Plain-English Summary

In software development, unacknowledged bugs compound into system crashes. In quantitative trading, unacknowledged bugs wipe out trading accounts.

This document represents the single, unvarnished ledger of **everything that is currently broken, missing, unreviewed, or vulnerable** across the ARGUS codebase. No issue is omitted to make the system look cleaner than it is. Each item includes:
- Its exact severity (CRITICAL, HIGH, MEDIUM)
- The agent who owns the fix
- The exact file and line number
- The technical failure mode
- The required remediation path

---

## 2. Master Numbered Open Problem Register

### Problem 1: Zero Strategies Qualified for Live or Paper Exploitation
- **Severity:** **CRITICAL** (Core Business Block)
- **Owner:** Claude (Lead Quantitative Researcher) / Antigravity
- **Location:** [`research/decision/register.json:1-99`](file:///C:/Users/yashw/swing-trades-track2/research/decision/register.json#L1-L99)
- **Failure Mode:** All 7 initial intraday strategies (`ORB_PROD`, `RESID_REV`, `COMPASS`, `LAST_LIGHT`, `TRAPDOOR`, `VOL_SQUEEZE`, `RECOIL`) have negative net expectancy after transaction costs (−0.06R to −0.15R). The desk has **0 qualified strategies**.
- **Action Plan:** Research and pre-register multi-day swing anomalies (Expiry Relief v2, PEAD v2, Short-Term Mean Reversion) where multi-day holding periods make transaction friction negligible.

---

### Problem 2: Core Strategy Framework Lacks an APPROVED Peer Review
- **Severity:** **CRITICAL** (Rule 8 Governance Block)
- **Owner:** Codex (Systems & Reliability Engineer) / Claude
- **Location:** [`shared/trust/reviews.jsonl:1-9`](file:///c:/Users/yashw/swing%20trades/shared/trust/reviews.jsonl#L1-L9)
- **Failure Mode:** In `shared/trust/reviews.jsonl`, the sole `APPROVED` commit in history is `CODEX-TRUST-P1-002` (`ff7f978`). Every subsequent commit in the research framework has received a verdict of `CHANGES_REQUIRED`. No prospective paper trading executed on recent commits can count toward qualification.
- **Action Plan:** Remediate failing probes identified by Codex and Claude on dedicated branches, verify test passes with exit code 0, and record clean `APPROVED` reviews in the ledger.

---

### Problem 3: T2-01 Fix `0020e8c` Rejected in Re-Check Review
- **Severity:** **CRITICAL** (Paper Desk Block)
- **Owner:** Claude / Codex
- **Location:** [`shared/trust/reviews.jsonl:9`](file:///c:/Users/yashw/swing%20trades/shared/trust/reviews.jsonl#L9) (`CODEX-T2-01-RECHECK`)
- **Failure Mode:** Commit `0020e8c` was initially marked `APPROVED` under `CODEX-T2-01`, but Codex superseded it 15 minutes later with `CHANGES_REQUIRED`. Failing probes demonstrated that paper plans could execute with unpinned surveillance evidence and concurrent shadow journal writes suffered race conditions.
- **Action Plan:** Author regression tests for unpinned evidence and journal file-locking in `swing-trades-track2`, apply fixes, and resubmit for Codex review.

---

### Problem 4: Legacy Production Strategy Engines Hardcode ₹58,333 Slot Cap & ₹1.75L Ceiling
- **Severity:** **CRITICAL** (Capital Sizing Breach)
- **Owner:** Antigravity (Quantitative Modeler)
- **Locations:**
  - [`antigravity/models/execution_policy.py:124`](file:///c:/Users/yashw/swing%20trades/antigravity/models/execution_policy.py#L124)
  - [`antigravity/models/liquid_momentum_screener.py:348`](file:///c:/Users/yashw/swing%20trades/antigravity/models/liquid_momentum_screener.py#L348)
  - [`antigravity/models/track2_portfolio_risk_governor.py:156, 446-451`](file:///c:/Users/yashw/swing%20trades/antigravity/models/track2_portfolio_risk_governor.py#L156)
  - [`antigravity/daemons/hybrid_execution_oms.py:95-100`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/hybrid_execution_oms.py#L95-L100)
- **Failure Mode:** Sizes positions at ₹58,333.33 (+53.5% above approved limit) and allows total exposure to reach ₹1,75,000 instead of Yashu's mandated ₹1,14,000 aggregate cap, increasing tail risk under market gaps.
- **Action Plan:** Staged on `track2/antigravity-adjusted-a1-staging`; submit review packet to Claude/Codex and merge into `main`.

---

### Problem 5: Plaintext API Secrets Committed in Repository
- **Severity:** **CRITICAL** (Security Breach)
- **Owner:** Antigravity / Yashu
- **Locations:**
  - [`antigravity/config/telegram_config.json:2-3`](file:///c:/Users/yashw/swing%20trades/antigravity/config/telegram_config.json#L2-L3)
  - [`antigravity/config/dhan_config.json:4-5`](file:///c:/Users/yashw/swing%20trades/antigravity/config/dhan_config.json#L4-L5)
- **Failure Mode:** Contains active Telegram bot tokens and a live Dhan JWT client token capable of placing real market orders or fund withdrawals if exposed.
- **Action Plan:** Yashu must rotate both credentials immediately. Code must load secrets exclusively from environment variables (`DHAN_ACCESS_TOKEN`, `TELEGRAM_BOT_TOKEN`) or external key files.

---

### Problem 6: Residual Kite / CDP Remote Debugging Scraping in Daemons
- **Severity:** **HIGH** (AGENTS.md Security Violation)
- **Owner:** Antigravity
- **Locations:**
  - [`antigravity/daemons/kite_web_depth_bridge.py:33, 517-545, 589`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/kite_web_depth_bridge.py#L33)
  - [`antigravity/daemons/track2_kite_bridge.py:41, 251, 260-270`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_kite_bridge.py#L41)
  - [`antigravity/daemons/bring_to_front.py:5-8`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/bring_to_front.py#L5)
- **Failure Mode:** Connects to ports 9333/9444 and executes browser cookie extraction, violating AGENTS.md Security Authorization rules.
- **Action Plan:** Permanently delete or neutralize Kite CDP scripts; migrate remaining feed consumers to the headless Dhan v2 / Upstox v2 bridge.

---

### Problem 7: Data Gap: 676-Day Gap in NSE Corporate Announcements
- **Severity:** **HIGH** (Strategy Research Block)
- **Owner:** Antigravity
- **Location:** `shared/track2_liquid/history/events/announcements.parquet`
- **Failure Mode:** File is continuous from 2021-10-01 to 2024-11-13, but completely empty from 2024-11-14 to 2026-09-24. Blocks catalyst-filtered ORB and holdout PEAD validation.
- **Action Plan:** Execute the approved nightly backfill under `OWNER-2026-09-29-03` ($\le 150$ requests/day, 21:00–07:00 IST, $\ge 4.0\text{s}$ pause).

---

### Problem 8: Data Gap: 118 Stock-Quarters Lack Timestamped BSE Results Filings
- **Severity:** **HIGH** (PEAD Holdout Block)
- **Owner:** Antigravity / Claude
- **Location:** `shared/track2_liquid/antigravity_staging/events_backup/bse_quarterly_results_filings_2021_2026.json`
- **Failure Mode:** While 1,510 of 1,628 stock-quarters (92.8%) have timestamped BSE filings, 118 have only board-meeting notices with date-only granularity, preventing exact known-at timing.
- **Action Plan:** Extract the 118 missing filing timestamps from the NSE announcements backfill.

---

### Problem 9: Daily Pipeline Calendar Ignores Holidays
- **Severity:** **HIGH** (Operational Flaw)
- **Owner:** Antigravity
- **Location:** [`scripts/daily_pipeline.py:103-121`](file:///c:/Users/yashw/swing%20trades/scripts/daily_pipeline.py#L103-L121)
- **Failure Mode:** `calculate_next_session_date` skips Saturdays and Sundays but lacks a gazetted exchange holiday calendar. On Thu 1 Oct, it will request 2 Oct (Gandhi Jayanti holiday), fail with 404, and never fetch Mon 5 Oct.
- **Action Plan:** Add an explicit NSE holiday calendar to `daily_pipeline.py`. On 1 Oct, operator must supply `--next-ban-date 2026-10-05`.

---

### Problem 10: Ingestor Evidence Age Window Blocks Friday-to-Monday Ingestion
- **Severity:** **HIGH** (Weekend Operational Block)
- **Owner:** Antigravity
- **Location:** [`antigravity/daemons/track2_official_source_ingestor.py:57, 400-401`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_official_source_ingestor.py#L57)
- **Failure Mode:** Enforces `MAX_EVIDENCE_AGE_DAYS = 1`. On Friday evening (e.g. 2026-10-02) for Monday (2026-10-05), difference is 3 days. Aborts with `"EVIDENCE_WINDOW_TOO_EARLY_FOR_SESSION"`.
- **Action Plan:** Update `MAX_EVIDENCE_AGE_DAYS` to align with the research reader's 4-day threshold, or schedule ingestion on Sunday evening post-18:00 IST.

---

### Problem 11: 2005–2021 Historical Archive Only ~10% Complete
- **Severity:** **MEDIUM** (Multi-Cycle Validation Gap)
- **Owner:** Antigravity
- **Location:** `shared/track2_liquid/history/raw/nse_archive/manifest.jsonl`
- **Failure Mode:** Only 2005, 2006, and Jan 2010 are on disk. Years 2007–2009 and 2011–2021 remain uncollected, preventing full 15-year statistical regime testing.
- **Action Plan:** Resume forward-download runs of `download_nse_archive.py` under the 500-request daily cap during off-market hours.

---

### Problem 12: Delivery MTO Data for 2022–2025 Missing on Disk
- **Severity:** **MEDIUM** (Strategy Feature Block)
- **Owner:** Antigravity
- **Location:** `shared/track2_liquid/history/delivery/raw/mto/`
- **Failure Mode:** Delivery percentage and institutional accumulation data is missing for 2022–2025, blocking delivery-spike anomaly research.
- **Action Plan:** Prioritize MTO file capture within the historical archive downloader.

---

### Problem 13: Stale Lock Recovery in `inbox_worker.py` Vulnerable to Cascading Deletions
- **Severity:** **HIGH** (Concurrency Race)
- **Owner:** Antigravity
- **Location:** [`antigravity/daemons/inbox_worker.py:149-181`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/inbox_worker.py#L149-L181)
- **Failure Mode:** In unpatched branches, `_break_stale_lock()` deleted locks based on file mtime without checking process liveness.
- **Action Plan:** Port the hardened `_pid_is_running` kernel check from `ops/nexus-daemon-resilience` across all branches.

---

### Problem 14: Downloader Pacing Lock Releases Pre-Fetch
- **Severity:** **HIGH** (Concurrency Violation)
- **Owner:** Antigravity
- **Location:** [`scripts/download_nse_archive.py:364`](file:///c:/Users/yashw/swing%20trades/scripts/download_nse_archive.py#L364)
- **Failure Mode:** Releases lock file before `session.get()` finishes, allowing two concurrent processes to make parallel requests and trigger 403 blocks.
- **Action Plan:** Hold the pacing lock through the entire HTTP round-trip until bytes are safely flushed to disk.

---

### Problem 15: Non-Atomic File Writes in Shadow Journal & Reconciliation
- **Severity:** **HIGH** (Data Corruption Risk)
- **Owner:** Claude / Codex
- **Locations:**
  - [`research/shadow/run_day.py:313-316, 488`](file:///C:/Users/yashw/swing-trades-track2/research/shadow/run_day.py#L313-L316)
  - [`research/shadow/reconcile.py:97-98`](file:///C:/Users/yashw/swing-trades-track2/research/shadow/reconcile.py#L97-L98)
- **Failure Mode:** Writes directly to open files. Process interruption mid-write produces truncated JSON lines, causing fatal `JSONDecodeError` on subsequent reads.
- **Action Plan:** Implement atomic replacement pattern (write to temporary file, flush/fsync, and `os.replace`).

---

### Problem 16: Symbol Regex Discrepancies Between Components
- **Severity:** **MEDIUM** (Data Ingestion Mismatch)
- **Owner:** Claude / Antigravity
- **Locations:**
  - Ingestor: `^[A-Z0-9&._-]+$` (allows dots)
  - [`market.py:386`](file:///C:/Users/yashw/swing-trades-track2/research/framework/market.py#L386): `^[A-Z0-9&_-]{1,25}$` (rejects dots)
  - `surveillance.py:30`: `^[A-Z0-9&\-]{1,20}$` (caps length at 20)
- **Failure Mode:** Valid official exchange symbols containing dots (e.g. `M&M.EQ`) or length > 20 cause reader rejection.
- **Action Plan:** Unify regex definitions across all modules to `^[A-Z0-9&._-]{1,30}$`.

---

### Problem 17: Draft Constitution v0.3 Unsigned & Not In Force
- **Severity:** **MEDIUM** (Governance Ambiguity)
- **Owner:** Yashu (Project Owner)
- **Location:** `CONSTITUTION.md` at commit `23c98a0`
- **Failure Mode:** `python -m research.trust.constitution status` exits with code 2 (`UNREVIEWED`). Agents operate without a formally signed constitutional boundary.
- **Action Plan:** Present Constitution v0.3 to Yashu for formal review, amendment, or sovereign signing.

---

### Problem 18: Live Shadow Universe Omits Surveillance Filter
- **Severity:** **CRITICAL** (Surveillance Leak)
- **Owner:** Claude / Codex
- **Location:** [`research/shadow/run_day.py:272-274`](file:///C:/Users/yashw/swing-trades-track2/research/shadow/run_day.py#L272-L274)
- **Failure Mode:** `live_universe()` calls `ub.build()` with `surveillance=None`. In `universe_build.py:85-88`, missing surveillance causes every stock to evaluate to `reason = "ELIGIBLE"`, allowing candidate signals on active ASM/GSM stocks.
- **Action Plan:** Enforce fail-closed requirement: `run_day.py` must load the verified surveillance snapshot and pass the set of banned symbols to `ub.build()`.
