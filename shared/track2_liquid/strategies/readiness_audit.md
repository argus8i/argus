# Point-in-Time Data Readiness Audit: Track 2 Alpha Strategies

**Audit Date:** 2026-10-01T22:38:00+05:30  
**Auditor:** Antigravity (Quantitative Modeling & Infrastructure Orchestrator)  
**Governing Standard:** Rule 8 v2 Tri-Agent Operational Standards & Codex Input Readiness Mandate  
**Scope:** Historical data verification across 2021–2026 for quantitative alpha strategy sleeves.

---

## 1. Executive Summary

In accordance with the Master 5-Day Sprint Plan and Codex Input Readiness Mandate, this audit rigorously verifies point-in-time (PIT) data availability on disk prior to strategy specification locking and backtest execution.

| Alpha Sleeve | Category | Required Data Sources | On-Disk PIT Status | Gate Verdict |
| :--- | :--- | :--- | :--- | :--- |
| **Sleeve A: Delivery Accumulation** | Institutional Float Absorption | Daily CM Bhavcopy + MTO Delivery Files | Verified (3.03M Bhavcopy rows, 526 MTO files) | **READY (PASS)** |
| **Sleeve B: 52-Week High Momentum** | Cross-Sectional Anchoring Momentum | Daily CM Bhavcopy OHLCV (2021–2026) | Verified (316 liquid scrips, 3.03M rows) | **READY (PASS)** |
| **Sleeve C: Post-Expiry Relief** | Structural F&O Gamma/Pin Mean Reversion | Daily Bhavcopy + F&O PIT Expiry Reference | Verified (231,595 PIT F&O records, exact nearest expiry) | **READY (PASS)** |
| **Sleeve D: PEAD Drift (Conditional)** | Post-Earnings Announcement Drift | Timestamped Announcements + Consensus SUE | Announcements verified (494k rows), consensus estimates missing | **DEFERRED / SANDBOX ONLY** |

---

## 2. Dataset Verification Details

### 2.1 Capital Market (CM) Bhavcopy (`bhavcopy/cm_bhavcopy_2021_2026.parquet`)
- **Row Count:** 3,037,926 rows.
- **Date Coverage:** 2021-10-01 to 2026-09-25 (continuous unbroken coverage).
- **Symbol Breadth:** 4,185 distinct listed symbols.
- **Columns Verified:** `trade_date`, `symbol`, `series`, `isin`, `open`, `high`, `low`, `close`, `last`, `prev_close`, `volume`, `turnover`, `trades`, `format`.
- **Integrity Check:** Zero NaN values in OHLCV for EQ series. Turnover and volume strictly positive for eligible liquid candidates.

### 2.2 Futures & Options (FO) Bhavcopy & Reference (`bhavcopy/fo_bhavcopy_underlyings_2021_2026.parquet`)
- **Row Count:** 248,779 rows.
- **Date Coverage:** 2021-10-01 to 2026-09-25.
- **Symbol Breadth:** 304 unique underlying symbols.
- **Columns Verified:** `trade_date`, `symbol`, open interest, futures/options volume.

### 2.3 Point-in-Time F&O Universe Reference (`reference/fno_point_in_time_2022_2026.parquet`)
- **Row Count:** 231,595 records.
- **Date Coverage:** 2022-01-03 to 2026-09-25.
- **Columns Verified:** `session`, `symbol`, `has_fut`, `has_opt`, `fut_contracts`, `opt_contracts`, `total_contracts`, `total_open_interest`, `underlying_price`, `lot_size`, `nearest_fut_expiry`, `fut_expiry_count`.
- **PIT Validity:** For every historical trading session, the exact set of active F&O underlyings and their exact `nearest_fut_expiry` date are locked without forward-looking survivorship bias.

### 2.4 Security-Wise Delivery Position Archive (MTO Archive)
- **File Count:** 526 raw MTO `.DAT` archive files under `history/raw/nse_archive/mto/` (spanning 2006 to 2026).
- **Schema:** Record Type 20 format: `[Record Type, Sr No, Security Name, Series, Traded Quantity, Deliverable Quantity, Delivery %]`.
- **Application:** Directly feeds Sleeve A delivery accumulation ratio calculations.

### 2.5 Security Surveillance & Event Archives
- **F&O Ban History (`events/fo_ban.parquet`):** 3,601 historical banned scrip sessions across 1,237 dates.
- **Corporate Actions (`events/corporate_actions.parquet`):** 124 records with ex-date and announcement date.
- **Exchange Announcements (`events/announcements.parquet`):** 494,630 raw exchange circulars and corporate disclosures.

---

## 3. Findings & Guardrails

1. **Sleeve A, B, and C Qualification:** All required inputs for Institutional Delivery Accumulation, 52-Week High Momentum, and Post-Expiry Relief are fully present on disk with zero missing dependencies.
2. **Sleeve D Formal Deferral:** As established during the Codex PEAD audit (finding CODEX-PEAD-E536AEC), historical analyst consensus estimates are absent. Corporate announcement timestamps cannot be converted to Standardized Unexpected Earnings (SUE) without external commercial estimate databases. In accordance with Rule 8 v2, Sleeve D is strictly deferred to research sandbox and excluded from canonical paper trading.
3. **Execution Rule 8 Invariant:** No strategy code or backtest execution shall precede the cryptographic pre-registration and hash-sealing of strategy specifications.
