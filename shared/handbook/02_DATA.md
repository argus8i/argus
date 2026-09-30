# 02. Data Architecture, Ingestion Pipelines & Historical Archives

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-30 15:30 IST  
**System Status:** **IN PROGRESS** (Job 0 operational; Announcements backfill approved; 2005–2021 archive ~10% complete)  
**Primary Data Storage:** `c:\Users\yashw\swing trades\shared\track2_liquid\history`  

---

## 1. Plain-English Summary

A quantitative trading desk lives or dies by its data. If our models receive data even a few minutes late, if prices are adjusted incorrectly for stock splits, or if surveillance lists are missing, the system will take trades that are impossible in the real market or buy stocks that cannot be exited.

In Project ARGUS, market data is strictly governed by three rules:
1. **Point-in-Time Reality:** We never use data before the exact moment it was published by the exchange. If an earnings filing arrived at 16:00 IST after market close, no model is allowed to use it during that day's session.
2. **Immutable Official Records:** We download raw data directly from official exchange archives (NSE and BSE), verify cryptographic hashes and ZIP checksums, and store them immutably.
3. **Fail-Closed Verification:** If any dataset (such as today's ASM/GSM surveillance list or tomorrow's F&O ban list) is missing, delayed, or corrupt, the trading desk halts automatically. It never assumes "no news is good news."

---

## 2. Daily After-Close Pipeline (JOB 0)

The daily pipeline ([`scripts/daily_pipeline.py`](file:///c:/Users/yashw/swing%20trades/scripts/daily_pipeline.py)) executes each evening following market close (recommended post-18:30 IST) to download official files for the completed trading day and prepare candidate universes for the next session.

### Datasets Captured Per Session
1. **CM Bhavcopy (`cm_bhavcopy`):** Official Capital Market cash segment daily prices, turnover, open, high, low, close.
   - Storage: `shared/track2_liquid/history/bhavcopy/raw/cm/<YYYY>/<YYYY-MM-DD>.csv.gz`
   - Official URL: `https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{yyyymmdd}_F_0000.csv.zip`
2. **F&O Bhavcopy (`fo_bhavcopy`):** Official Futures & Options segment daily settlement, open interest, and contract volumes.
   - Storage: `shared/track2_liquid/history/bhavcopy/raw/fo/<YYYY>/<YYYY-MM-DD>.csv.gz`
   - Official URL: `https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip`
3. **Security-Wise Delivery Positions (`mto`):** Delivery quantities and percentages (MTO.DAT).
   - Storage: `shared/track2_liquid/history/delivery/raw/mto/<YYYY>/<YYYY-MM-DD>.dat.gz`
   - Official URL: `https://nsearchives.nseindia.com/archives/equities/mto/MTO_{ddmmyyyy}.DAT`
4. **Next Session's F&O Ban List (`fo_secban`):** Securities subject to derivative trading bans for the upcoming session.
   - Storage: `shared/track2_liquid/history/raw/nse/fo_ban/<D>_<D>.csv.gz`
   - Official URL: `https://nsearchives.nseindia.com/archives/fo/sec_ban/fo_secban_{ddmmyyyy}.csv`

### Reader Path Alignment
An audit by Claude on 29 September 2026 ([`shared/reviews/claude_job0_audit_2026-09-29.md:15-28`](file:///c:/Users/yashw/swing%20trades/shared/reviews/claude_job0_audit_2026-09-29.md#L15-L28)) confirmed that `daily_pipeline.py` output paths exactly match the research reader paths in `research/framework/market.py`.

### Critical Operating Caveat: Holiday Calendar Gap
As identified in Claude's audit ([`claude_job0_audit_2026-09-29.md:34-48`](file:///c:/Users/yashw/swing%20trades/shared/reviews/claude_job0_audit_2026-09-29.md#L34-L48)), `calculate_next_session_date` ([`daily_pipeline.py:103-121`](file:///c:/Users/yashw/swing%20trades/scripts/daily_pipeline.py#L103-L121)) lacks an automated holiday calendar:
- On Thursday, 1 October 2026, default execution will request the ban list for Friday, 2 October (Gandhi Jayanti exchange holiday), receive a 404, and never request Monday, 5 October.
- **Required Operator Guardrail:** For Thursday 1 October, JOB 0 must be executed with explicit ban date override:
  ```powershell
  python scripts/daily_pipeline.py --trade-date 2026-10-01 --next-ban-date 2026-10-05 --pause 4.0
  ```

---

## 3. Official Surveillance Ingestion (ASM/GSM)

Rule 6 and Rule 11 mandate daily surveillance verification. Any scrip entering Additional Surveillance Measure (ASM) Stages 1–4, Graded Surveillance Measure (GSM), or Trade-to-Trade (`BE`) must be frozen immediately.

### Ingestion Architecture
- **Daemon:** [`antigravity/daemons/track2_official_source_ingestor.py`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_official_source_ingestor.py)
- **Snapshot Storage:** `shared/track2_liquid/surveillance/nse_surveillance_snapshot_<date>.json`
- **Raw Evidence:** Storage of immutable raw API responses with content hashes (`raw_nse_asm_<date>_<hash>.json`, `raw_nse_gsm_<date>_<hash>.json`).
- **Research Bridge:** [`antigravity/daemons/track2_surveillance_bridge.py`](file:///c:/Users/yashw/swing%20trades/antigravity/daemons/track2_surveillance_bridge.py) creates cryptographic hard-links/copies into the reader paths.

### Timing & Validity Constraints (Research Reader Contract)
Per research reader specifications in `research/framework/market.py:162-202`:
- **Allowed Publication Window:** An official surveillance snapshot for session $S$ must be fetched **between 16:00 IST on $S-1$ and 08:59:59 IST on $S$**.
- **Cryptographic Verification:** The reader rejects bare unverified JSON files; it requires raw byte hashes, HTTP 200 statuses, and parsed symbol lists that match raw file contents.
- **Current Snapshot State:** The official snapshot for 30 September 2026 (`nse_surveillance_snapshot_2026-09-30.json`) is generated and present on disk.

---

## 4. NSE Corporate Announcements Backfill & Daily Capture

### The Announcement Gap
Analysis in [`shared/reviews/claude_pead_orb_data_inventory_2026-09-29.md:14`](file:///c:/Users/yashw/swing%20trades/shared/reviews/claude_pead_orb_data_inventory_2026-09-29.md#L14) identified that `events/announcements.parquet` was continuous from 1 October 2021 to 13 November 2024, followed by a **676-day gap** through September 2026. This data gap blocks catalyst-filtered ORB and post-earnings drift research.

### Owner Approval & Operating Conditions
In `OWNER-2026-09-29-03` ([`shared/governance/owner_decisions.jsonl:3`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L3)), Yashu approved the announcements backfill subject to **Claude's 4 Mandatory Conditions**:
1. **Immediate Latch on First Error:** Stop immediately on the FIRST HTTP 401, 403, or 429 response and latch that host for the remainder of the calendar day. Never retry a blocked endpoint.
2. **Paced Request Budget:** Total API requests to `www.nseindia.com` (combining backfill, surveillance ingestor, and probes) must stay strictly $\le 150$ per day. Backfill takes at most ~140 requests per night, executing strictly between **21:00 and 07:00 IST**, $\ge 4.0\text{s}$ apart, serialized one at a time.
3. **Pre-Flight Proof (2024-11-13):** On the first night, fetch a known date already on file (`2024-11-13`) and prove a single request returns every announcement for that day, matching row counts against `events/announcements.parquet`. If row counts do not match, abort immediately.
4. **Verified Coverage Bounds:** Acknowledge that the earlier claim of "1,134 missing results" was incorrect; only **118 stock-quarters** lack timestamped results filings.

Probe verification script: [`scripts/check3_announcements_probe.py`](file:///c:/Users/yashw/swing%20trades/scripts/check3_announcements_probe.py).

---

## 5. BSE Quarterly Earnings Results Filings

### Data Reconciliation (118 Missing Filings vs 1,134 Intimations)
A critical audit finding on 29 September resolved an earlier misconception:
- Earlier audits cited "1,134 missing timestamps."
- Claude's re-examination ([`claude_pead_orb_data_inventory_2026-09-29.md:68-70`](file:///c:/Users/yashw/swing%20trades/shared/reviews/claude_pead_orb_data_inventory_2026-09-29.md#L68-L70)) revealed that the 1,134 figure counted BSE board-meeting notices (`BSE_OFFICIAL_BOARD_MEETING`), which carry session dates rather than filing timestamps.
- For actual Regulation 33 earnings result filings (`EARNINGS_RESULT`) across the 2024Q4–2026Q3 holdout: **1,510 of 1,628 stock-quarters (92.8%) already have timestamped BSE filings on disk.** Only 118 stock-quarters lack results.

### Source Approval & File Provenance
- Yashu approved BSE official results filings as PEAD's timestamp source in `OWNER-2026-09-29-01` ([`shared/governance/owner_decisions.jsonl:1`](file:///c:/Users/yashw/swing%20trades/shared/governance/owner_decisions.jsonl#L1)).
- Staging file: `shared/track2_liquid/antigravity_staging/events_backup/bse_quarterly_results_filings_2021_2026.json` (SHA-256: `fee9e964...` LF / `3adefca9...` CRLF; 8,120 filings across 208 symbols).

---

## 6. 2005–2021 Historical NSE Bhavcopy Archive

To evaluate quantitative edges across unseen market cycles, the archive downloader ([`scripts/download_nse_archive.py`](file:///c:/Users/yashw/swing%20trades/scripts/download_nse_archive.py)) downloads historical CM, F&O, and MTO files forward in time from 2005 to 2021.

### Current Coverage State
- **Storage:** `shared/track2_liquid/history/raw/nse_archive/`
- **Manifest:** `manifest.jsonl` currently contains **2,539+ lines**.
- **Coverage on Disk:** Years **2005, 2006, and January 2010** have been collected. Years 2007–2009 and 2011–2021 remain uncollected (~10% total archive complete).

### Integrity Invariants Enforced in Code
- **Corrupt ZIP Detection:** Uses Python's native `zipfile.ZipFile.testzip()` to inspect CRC-32 checksums of every downloaded archive, catching truncated or corrupted downloads before extraction.
- **Internal Date Verification:** Inspects the internal `TradDt` / `TIMESTAMP` column in Bhavcopy files and the `Trade Date` header in MTO files. Files with header/content date mismatches are classified as `FAILED_WRONG_DATE` and never saved.
- **Fake Success Rejection:** If NSE returns HTTP 200 with an HTML error page or maintenance notice, the response is rejected as `FAILED_BAD_CONTENT`.
- **Cross-Process Concurrency Locks:** Downloads acquire `.pacing.lock` and enforce a minimum 4.0-second delay between requests across all running instances via `.pacing_clock`.

---

## 7. Known Data Deficiencies & Blockers Summary

| Dataset | Period / Scope | Defect / Blocker | Remediation Path |
|---|---|---|---|
| **Delivery Positions (MTO)** | 2022–2025 | Missing from disk; `DATA_BLOCKED` for delivery accumulation strategies | Collect via paced archive downloader |
| **NSE Announcements** | 2024-11-14 to 2026-09-24 | 676-day gap in `events/announcements.parquet` | Execute approved nightly backfill under `OWNER-2026-09-29-03` |
| **BSE Earnings Results** | 2024Q4 to 2026Q3 | 118 stock-quarters lack timestamped result filings | Extract from NSE announcements backfill |
| **Daily Calendar** | October 2026 | No automated holiday calendar; ignores Gandhi Jayanti & Dussehra | Pass `--next-ban-date` manually on holiday eves |
| **Ingestor Evidence Window** | Weekends | `MAX_EVIDENCE_AGE_DAYS = 1` blocks Friday ingestion for Monday | Run ingestor on Sunday evening or relax limit to 4 days |
