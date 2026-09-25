# P3 report: data layer

**Date:** 2026-09-25
**Branch:** `track2/decision-engine`
**Author:** Claude (red team)

The code was written by an unidentified session and adopted by Claude on Yashu/Antigravity's instruction (25 Sep). The changes made at adoption are listed in section 6.

**Verdict:**
- **The P3 code is complete except P3.2–P3.4.**
- **P3 acceptance FAILS on data.**
  - The only history available is Yahoo Finance data. It fails the cross-source check.
  - It is now enforced as QA_ONLY: no strategy can read it.

Every number here was re-run on 25 Sep in this worktree. The outputs are under `research/outputs/p3/` (gitignored):
```
python -m research.data.ingest_json --input <main>/shared/track2_liquid/historical_candles_fno_210.json --input <main>/shared/track2_liquid/historical_indices.json
python -m research.data.validate --report research/outputs/p3/validation.json
python -m research.data.cross_source --reference shared/track2_liquid/historical_candles_track2.json --out research/outputs/p3/cross_source.json
```

## 1. Inputs and provenance

| Input | Source (MEASURED from the `source` field) | Class | Strategy-eligible |
|---|---|---|---|
| `historical_candles_fno_210.json` (Antigravity, a8264e9) | `YahooFinance_ChartAPI_v8` | YAHOO_CHART_V8 | **No** (QA_ONLY) |
| `historical_indices.json` (Antigravity, 94db5ab) | Yahoo chart tickers (`^NSEI`, `^CNXIT`, …) | YAHOO_CHART_V8 | **No** (QA_ONLY) |
| `historical_candles_track2.json` (Kite, 32 sessions) | `kite.zerodha.com/oms` web session | KITE_WEB_SESSION | Regression reference only |

**How provenance is enforced** (`research/data/provenance.py`):
- Every parquet row carries a `source` class.
- `ParquetCandleStore` refuses to open any history containing a QA_ONLY class in STRATEGY mode.
- Only `DHAN_API_V2` and `SYNTHETIC` are eligible.
- Files with no source column count as `UNKNOWN`, which is QA_ONLY.
- Tests: `test_provenance_classes`, `test_qa_only_sources_never_open_in_strategy_mode`, `test_one_qa_only_symbol_blocks_the_whole_history`, `test_parquet_without_source_column_is_unknown`.

## 2. Coverage (MEASURED, `validation.json`)

| Item | Value |
|---|---|
| Symbols | 225: 210 tradables and 15 indices |
| 15m sessions | 58, from 2026-07-06 to 2026-09-24 (the partial 25 Sep session was dropped at ingest) |
| Pre-CAS sessions | 20. All fall in the plan's **holdout** (P7.1), so they are hidden in STRATEGY mode. |
| Post-CAS sessions | 38. **Excluded** from design and holdout (decision 2), so descriptive only. |
| **Design-set sessions** | **0** |
| Symbol-sessions structurally valid | 13,050 of 13,050 (bar count, contiguity, OHLC, volume) |
| Dropped at ingest | 5,730 post-CAS 15:15 auction bars, 4,343 bars of the incomplete 25 Sep session, 242 misaligned bars (13:35, 13:47–13:50) |

**Acceptance line 1** (≥ 95% valid sessions for ≥ 90% of the universe) passes *structurally*. It does not matter while the data has no design-set sessions.

**Daily bars vs intraday bars (a flag, not an invalidation):**
- The intraday high/low equals the daily high/low within one tick on only **11.9%** of sessions.
- Intraday volume sums to a median **94.5%** of daily volume (p5 84.3%).

Both fit the auction prints being missing from Yahoo's intraday bars.

## 3. Cross-source check, P3.9 (MEASURED, `cross_source.json`): **FAILED**

The comparison covers the 9 shared symbols over 288 sessions and 6,944 bars.

| Check | Result |
|---|---|
| Bar starts identical | yes: 0 sessions mismatched, 0 one-bar offsets (the parser is correct) |
| OHLC within 1 tick | **no**: open 23.5%, high 12.4%, low 15.0% and close 13.8% of bars mismatch |
| Volume within 1% | **no**: 6.7% of bars mismatch |
| Where it concentrates | The **09:15 bar**: 898 of 4,964 mismatches, with open, high and volume all affected. Yahoo leaves out the pre-open auction print, so its 09:15 volume is about 45–65% of Kite's. The 15:00 bar is second. Mid-session bars are off by about 1–3 ticks. |

**Consequence:** any ORB opening range, and any first-bar or volume statistic, computed from Yahoo is wrong in a systematic direction. That is why the class is QA_ONLY, not just "noisy".

## 4. Events coverage (P3.4): **none**

- `research/data/nse_events.py` is **not built**. `research/features/events.py` has only `NoEventsData` and an in-memory `TableEvents`.
- With `NoEventsData`, every news answer is `None`, so RESID_REV blocks every signal (`NEWS_BLOCKED:EVENTS_UNKNOWN`).
- Only the registered variant `RESID_REV_NF` can be evaluated, and every report must say so (plan P3.4 fallback).
- Decision 7 approved NSE website APIs at 1 request per 2 s. The fetcher is the next P3 item.

## 5. Universe and sector caveats

**Universe table (`universe_daily.parquet`): not built.**
- `universe_build` opens the history in STRATEGY mode, and the provenance rule now refuses the Yahoo history. That is correct: the table feeds strategies.
- On the next approved history it will carry these labels:
  - `FNO_MEMBERSHIP_CURRENT_LIST`: survivorship bias. There is no inclusion/exclusion history yet.
  - `MCAP_BAND_NOT_APPLIED` (decision 6).
  - `FO_BAN_HISTORY_MISSING` (P3.4 archives not fetched).
  - `SURVEILLANCE_HISTORY_MISSING`.

**Sector map (`history/reference/sector_map.csv`): built.** 210 rows, 51 of them on the NIFTY 50 fallback.
- **The mapping is an ASSUMPTION:** Antigravity's static 28-industry classification is mapped to the sectoral indices through a fixed table. The classification itself was not independently checked.
- **No index weights:** the plan's ">10% weight → NIFTY 50" rule cannot be applied, so heavyweights are partly regressed on themselves.
- **No bias on the fallback:** a missing index bar on a date falls back to NIFTY 50 and is counted.

## 6. What changed at adoption

| Change | Why |
|---|---|
| `provenance.py`, source stamping in `ingest_json`, the store refusal, and the Dhan exporter labelling its output `DHAN_API_V2` | QA_ONLY enforcement (Antigravity/Yashu instruction; plan rule 1.2.11) |
| `prereg_io.normalised_sha256` now hashes bytes with CRLF → LF; `test_lock_verification` writes LF bytes and adds a stray-CR case | The Windows failure: the test wrote CRLF and then converted it again, giving CR CR LF |
| P2/P4/P5 tests read the restored `shared/.../historical_candles_track2.json` under the SHA-256 pin, instead of a 1.5 MB committed copy | Plan rule 1.2.6 caps committed fixtures at 200 KB. The pin makes any future overwrite fail loudly. The copy was moved to `research/outputs/p3/unused_fixture/` (gitignored), not deleted. |
| `trials_registry.csv` T0022 (ORB_PROD) and T0023 (RESID_REV_NF scan) | The adopted session's descriptive runs on Yahoo data were not registered (rule 1.2.10) |
| RESID_REV "next trading day": exact with a calendar; without one, the next two weekdays are checked | Previously the next weekday only, so a holiday could hide a scheduled result (fail-open) |

## 7. Not done in P3, and why

| Item | Status |
|---|---|
| P3.2 `instruments.py` and P3.3 `dhan_history.py` | **Deferred.** Antigravity (25 Sep) says the Dhan Data API will not be bought and proposes a Hugging Face mirror of Upstox 1-minute data instead. Dhan-specific code waits for Yashu's decision on the source. The cdc0665 fetcher exists and now labels its output. |
| The Hugging Face proposal | **Not accepted as a source yet.** It changes plan rule 1.2.11. Before any use it needs: (1) Yashu's explicit approval of the source, and a check of the dataset's licence and redistribution terms; (2) a record of how 1-minute bars aggregate to 15-minute bars and which timestamp convention they use; (3) a check of whether the 09:15 bar includes the pre-open auction; (4) passing P3.9 against Kite; (5) coverage of the design window (Oct 2021 – Sep 2024; the dataset reportedly starts in 2022). Until then it is classified `HF_UPSTOX_MIRROR` (QA_ONLY). |
| P3.4 `nse_events.py` | Not built; next. |
| `paths.history_dir()` default | It is `<repo>/shared/track2_liquid/history`, which is the worktree in a worktree. The plan's default is the main checkout. Both are gitignored. Set `TRACK2_HISTORY_DIR` to share one history. |

**Acceptance:** code yes; data **no** (the cross-source check fails and there are no design sessions). P7 cannot start until an approved, cross-checked intraday source covers the design window.
