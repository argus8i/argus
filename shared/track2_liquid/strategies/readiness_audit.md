# Point-in-Time Data Readiness Audit: Track 2 Alpha Strategies

**Audit Date:** 2026-10-01T22:54:00+05:30  
**Auditor:** Antigravity (Quantitative Modeling & Infrastructure Orchestrator)  
**Governing Standard:** Rule 8 v2 Tri-Agent Operational Standards & Codex Input Readiness Mandate  
**Scope:** Historical data verification across 2021–2026 for quantitative alpha strategy sleeves.

---

## 1. Executive Summary

In accordance with the Master 5-Day Sprint Plan and Codex Input Readiness Mandate (Findings 1 & 5), this audit rigorously verifies point-in-time (PIT) data availability and historical coverage constraints prior to strategy execution.

| Alpha Sleeve | Category | Required Data Sources | On-Disk PIT Status & Coverage Reality | Gate Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Sleeve A: Delivery Accumulation** | Institutional Float Absorption | Daily CM Bhavcopy + MTO Delivery Files | 526 MTO files total; 2021–2026 sparse (5 files). Coverage concentrated in 2005–2006 baselines and live daily collector. | **DATA-CONSTRAINED / SCOPED COVERAGE** |
| **Sleeve B: 52-Week High Momentum** | Cross-Sectional Anchoring Momentum | Daily CM Bhavcopy OHLCV (2021–2026) | Verified Continuous (316 liquid scrips, 3,037,926 rows, 2021-10-01 to 2026-09-25) | **READY (PASS)** |
| **Sleeve C: Post-Expiry Relief** | Structural F&O Gamma/Pin Mean Reversion | Daily Bhavcopy + F&O PIT Expiry Reference | Verified Continuous (231,595 PIT F&O records, exact nearest expiry, 2022-01-03 to 2026-09-25) | **READY (PASS)** |
| **Sleeve D: PEAD Drift (Conditional)** | Post-Earnings Announcement Drift | Timestamped Announcements + Consensus SUE | Announcements verified (494k rows), consensus analyst estimates missing | **DEFERRED / SANDBOX ONLY** |

---

## 2. Dataset Verification Details & Coverage Realities

### 2.1 Capital Market (CM) Bhavcopy (`bhavcopy/cm_bhavcopy_2021_2026.parquet`)
- **Row Count:** 3,037,926 rows (independently verified by Codex).
- **Date Coverage:** 2021-10-01 to 2026-09-25 (continuous unbroken daily coverage).
- **Symbol Breadth:** 4,185 distinct listed symbols.
- **Columns Verified:** `trade_date`, `symbol`, `series`, `isin`, `open`, `high`, `low`, `close`, `last`, `prev_close`, `volume`, `turnover`, `trades`, `format`.
- **Integrity Check:** Zero NaN values in OHLCV for EQ series. Turnover and volume strictly positive for eligible liquid candidates.

### 2.2 Futures & Options (FO) Bhavcopy & Reference (`bhavcopy/fo_bhavcopy_underlyings_2021_2026.parquet`)
- **Row Count:** 248,779 rows (independently verified by Codex).
- **Date Coverage:** 2021-10-01 to 2026-09-25.
- **Symbol Breadth:** 304 unique underlying symbols.
- **Columns Verified:** `trade_date`, `symbol`, open interest, futures/options volume.

### 2.3 Point-in-Time F&O Universe Reference (`reference/fno_point_in_time_2022_2026.parquet`)
- **Row Count:** 231,595 records (independently verified by Codex).
- **Date Coverage:** 2022-01-03 to 2026-09-25.
- **Columns Verified:** `session`, `symbol`, `has_fut`, `has_opt`, `fut_contracts`, `opt_contracts`, `total_contracts`, `total_open_interest`, `underlying_price`, `lot_size`, `nearest_fut_expiry`, `fut_expiry_count`.
- **PIT Validity:** For every historical trading session, the exact set of active F&O underlyings and their exact `nearest_fut_expiry` date are locked without forward-looking survivorship bias.

### 2.4 Security-Wise Delivery Position Archive (MTO Archive Reality Audit)
Per Codex Finding 1, raw MTO `.DAT` file count breakdown across historical archives:
- **Total Files:** 526 MTO files (`history/raw/nse_archive/mto/`).
- **Date Range:** 2005-01-03 to 2026-10-01.
- **Yearly Distribution:**
  - 2005: 251 files
  - 2006: 250 files
  - 2010: 19 files
  - 2016: 1 file
  - 2021: 1 file
  - 2026: 4 files (2026-09-28, 2026-09-29, 2026-09-30, 2026-10-01)
- **Coverage Assessment for Sleeve A:**
  The 5 MTO archives in 2021–2026 do not establish unbroken 20-session delivery moving average histories across the 2021–2025 backtest window.
  Consequently, **Sleeve A evaluation is strictly restricted to verified join-coverage sessions** (2005–2006 historical baseline validation, and ongoing prospective sessions ingested daily via `auto_daily_collector.py`). Multi-year backtesting across 2021–2025 cannot claim full Sleeve A coverage until historical MTO archives for 2021–2025 are back-filled.

### 2.5 Security Surveillance & Event Archives
- **F&O Ban History (`events/fo_ban.parquet`):** 3,601 historical banned scrip sessions across 1,237 dates.
- **Corporate Actions (`events/corporate_actions.parquet`):** 124 records with ex-date and announcement date.
- **Exchange Announcements (`events/announcements.parquet`):** 494,630 raw exchange circulars and corporate disclosures.

---

## 3. Operational Directives & Guardrails

1. **Sleeve B (52-Week High Momentum) & Sleeve C (Post-Expiry Relief):** 100% continuous data readiness verified across 2021–2026 on disk. Full rolling walk-forward backtesting can proceed without data constraints.
2. **Sleeve A (Delivery Accumulation):** Formally flagged as data-constrained for 2021–2025. Backtest evaluation is strictly scoped to sessions with verified MTO data joins; live paper trading runs on daily collected MTOs.
3. **Sleeve D (PEAD):** Strictly deferred to research sandbox due to absent historical consensus earnings estimates (CODEX-PEAD-E536AEC).
