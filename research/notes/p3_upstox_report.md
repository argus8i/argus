# P3 addendum: Upstox V2 historical candles as the P7 intraday source

**Date:** 2026-09-25
**Author:** Claude (red team)
**Module:** `research/data/upstox_history.py`

**Verdict: FAIL.**
- Upstox V2 does **not** pass the Kite cross-check (P3.9). It stays **QA_ONLY** in `research/data/provenance.py`.
- Per Yashu's instruction, the 2022–2026 universe was **not ingested**.
- The fetcher, resampler and cross-check tooling are built and tested. Any future source can run the same check.

## 1. The claims, checked (MEASURED, 25 Sep)

| Claim | Result |
|---|---|
| Free and unauthenticated | **True.** Plain GET with no token; HTTP 200. |
| Covers Jan 2022 – Sep 2026 | **True.** SUZLON 1-minute 2022-01-03: 375 bars. Daily bars from 2021-06-01 in one request. The design window starts Oct 2021, so Oct–Dec 2021 would have no 1-minute data even if the source passed. |
| Range per request | A calendar month of 1-minute bars works (Aug 2026: 7,875 = 21 × 375). 41 days is refused (UDAPI1148). |
| Includes the 09:15 pre-open auction print | **True for prices.** 0 of 277 stock-sessions differ from Kite on the 09:15 open. SUZLON 2026-08-10 opens at 48.33 in both. |
| Matches Kite's 09:15 OHLC to the paisa | **Open: yes.** **Volume: no.** The Upstox 09:15 volume is systematically 1.2–3.8% above Kite; 248 of 277 exceed the 1% tolerance. |
| Instrument master | 79,346 rows; equities keyed by ISIN (`NSE_EQ|<ISIN>`). All 8 stocks and NIFTY 50 resolved. |

## 2. Cross-source check (plan P3.9) against the Kite 32-session file

**Command:**
```
python -m research.data.upstox_history xcheck --reference shared/track2_liquid/historical_candles_track2.json --report research/outputs/p3/upstox_cross_source.json
```
**Result:** exit code **1** (fail). The run was repeated after the final resampler change, with identical numbers.

**Coverage:** 9 shared series (8 stocks + NIFTY 50) × 32 sessions = 288 series-sessions, 6,944 bars. That is 108,000 one-minute rows: every minute present, no duplicates, 256 post-CAS 15:15 auction bars dropped as for every source.

| Check (plan P3.9) | Result |
|---|---|
| Identical bar starts | **pass**: 0 mismatches, 0 one-bar offsets |
| OHLC within one tick | **fail**: open 1,970 (28.4%), high 844 (12.2%), low 878 (12.6%), close 955 (13.8%) of bars |
| Volume within 1% | **fail**: 837 bars (12.1%) |
| NIFTY 50 (index tolerance 0.01%) | **pass**: 0 mismatches |

**Size of the price mismatches** (in ticks of the reference price): median 4, p90 8, p99 18, max 57. They occur in every slot. The 09:15 bar is best (75 mismatching fields over 277 bars, none of them the open). The 15:00 bar is worst (364).

**The resampling convention is not the cause.** On 4 stocks, re-bucketing with the minute stamp read as the minute's end (shift ±1 minute) is far worse: open mismatches 63–69% against 27% as built. The specified convention [t, t+14] is the best fit, and the disagreement is in the source data.

## 3. Which source is right? A third witness

Kite is not ground truth by definition, so every bar where Upstox and Kite disagree by more than a tick was also compared with the (QA_ONLY) Yahoo bars for the same symbol, time and field:

| Field | Yahoo agrees with Kite | Yahoo agrees with Upstox | Neither | Both |
|---|---|---|---|---|
| open | 1,024 | 709 | 216 | 21 |
| high | 679 | 72 | 93 | – |
| low | 717 | 94 | 67 | – |
| close | 703 | 168 | 78 | 6 |
| **all** | **3,123** | **1,043** | 454 | 27 |

**Direction:** Upstox's bar **ranges are wider** than Kite's.
- Its high is above Kite's in 757 of 844 high mismatches.
- Its low is below Kite's in 764 of 878 low mismatches.
- Yahoo sides with Kite about 8:1 on highs and lows.

**Consequence for P7:** stops and targets are simulated on bar highs and lows. Wider ranges would trigger both more often than the market did, which biases every R statistic. That is exactly what P3.9 exists to catch.

## 4. What is built (and tested)

**`upstox_history.py` does:**
- master download and ISIN resolution, with canonical index names mapped to Upstox index names;
- a polite client: 1 request/s, backoff, and a stop after 3 consecutive 401/403/429 or transport failures;
- a resumable monthly fetch with a raw gzip store and a manifest (URL hash only);
- the specified resampling. Conflicting duplicate minutes are dropped whatever the input order;
- ingest through `ingest_json`, with the same drop rules and provenance stamp;
- `xcheck`.

**`provenance.py`:** new class `UPSTOX_API_V2`, QA_ONLY, kept distinct from `HF_UPSTOX_MIRROR`.

**`cross_source.compare`:** now fails on **any** bar-start mismatch and on an empty comparison. Previously only one-bar offsets failed, which could have passed a source with missing bars.

**Tests:** `research/tests/test_upstox_history.py`, 9 tests, no network. Full suite: **525 passed**.

## 5. Options for Yashu (decision needed; nothing enabled)

1. **Keep P3.9 as written and use a source that passes.** Dhan Data API (₹499/month, the plan's original) must run the same `xcheck`-style comparison before use.
2. **Change the acceptance rule.** For example, accept Upstox for opens and closes but not for highs, lows or volume. That would need a written policy and a P7 fill model that does not rely on bar extremes. **I do not recommend this.** Stops and targets are the extremes.
3. **Ask Upstox how 1-minute candles are built.** They may include trades that Kite's 15-minute candles exclude, such as odd-lot or special-session prints. That could explain wider ranges and the higher 09:15 volume. Until it is explained, the data stays QA_ONLY.

**Nothing here changes the P7 blocker.** There is still no strategy-eligible intraday history for the design window (Oct 2021 – Sep 2024).

## 6. Re-run under Yashu's multi-broker tolerance (25 Sep 2026, later)

**Policy change (Yashu):**
- OHLC tolerance is now max(2 ticks, 0.20%). The plan's P3.9 said one tick.
- Volume stays within 1%, and indices within 0.01%.
- Implemented as the defaults in `research/data/cross_source.py`; `compare(..., price_tol_ticks=1, price_tol_pct=0)` restores the original rule.

**Re-run:**
```
python -m research.data.upstox_history xcheck --reference shared/track2_liquid/historical_candles_track2.json --report research/outputs/p3/upstox_cross_source_tol020.json
```
**Result:** exit **1**, still **FAIL**.

| Check | Result |
|---|---|
| Bar starts | pass |
| Price | **18 fields** in 17 bars out of 6,944 still exceed max(2 ticks, 0.20%). They are open 3, high 4, low 3, close 8, mostly in the 15:00 bar. The worst are COCHINSHIP 2026-09-22 15:00 high +41 bps, ANGELONE 2026-09-18 15:00 close −40 bps and SUZLON 2026-08-25 15:00 low −30 bps. |
| Volume | **837 bars** outside 1% (248 of them at 09:15, where Upstox is systematically 1.2–3.8% higher); 517 outside 2%, 155 outside 5%, 42 outside 10% |

**Range bias (MEASURED, 6,144 stock bars):**
- Upstox's bar range is wider on 35.9% of bars and narrower on 5.4%.
- Median range ratio 1.000; mean 1.037.
- Extra range averages 1.1 bps (p90 4.2 bps).
- It is one-directional, but small: about 1% of a typical 1% stop distance.
- **About the "tick sampling" explanation:** sampling would make bars narrower, not wider, so it does not explain the direction.

**Status:** `UPSTOX_API_V2` is still QA_ONLY and nothing has been ingested. Marking it passed needs two decisions that the tolerance change does not cover:
- the volume tolerance;
- what to do with the 18 remaining price outliers.

## 7. Final gate under Yashu's rule, promotion and ingest verification (25 Sep 2026)

### The rule
Yashu's answers: volume is reported, not gated; each field must agree on at least 99.5% of bars.
- **Stocks:** bar starts identical, and each OHLC field within max(2 ticks, 0.20%) on ≥ 99.5% of bars.
- **Indices:** no mismatch at all (0.01%).
- **Volume:** reported only.
- **Implementation:** the defaults of `research/data/cross_source.py`. The plan's original rule remains available: `compare(..., price_tol_ticks=1, price_tol_pct=0, min_agreement=1.0, gate_volume=True)`.

### Re-run
```
python -m research.data.upstox_history xcheck --reference shared/track2_liquid/historical_candles_track2.json --out-root research/outputs/p3/upstox_xcheck_final --report research/outputs/p3/upstox_cross_source_final.json
```
**Result: exit 0, PASSED.**

| Check | Result |
|---|---|
| Stock field agreement | open 99.95%, high 99.93%, low 99.95%, close 99.87% |
| Bar starts | identical |
| NIFTY 50 | exact |
| Volume outside 1% | 837 bars (reported, not gated) |

**Promotion:** `UPSTOX_API_V2` is now in `STRATEGY_SOURCES` (`research/data/provenance.py`), on Yashu's instruction. The Hugging Face mirror, Yahoo, Kite and unlabelled data stay QA_ONLY.

### Ingest: the 228-series history built by Antigravity
Antigravity's `scripts/fast_upstox_ingest.py` calls `research.data.upstox_history.build`.

| Check | Result |
|---|---|
| Raw integrity | all 13,251 manifest records HTTP 200; every file present; every body SHA-256 matches (13,014 one-minute months + 237 daily; 228 series). `research/outputs/p3/upstox_raw_integrity.json` |
| Resampling | 97.18 M minutes. 108,000 identical duplicates (the cross-check windows overlapping the monthly ones), **0 conflicts**. 43,206 minutes outside 09:15–15:29 were dropped. 7,980 post-CAS auction bars were dropped. |
| Validation | 228 series, 1,170 sessions (2022-01-03 to 2026-09-24), 259,815 series-sessions. **99.67% valid.** **209 of 210 stocks** have ≥ 95% valid sessions (worst: GVT&D 92.7%), so P3 acceptance line 1 **passes**. |
| Invalid sessions | mainly special short sessions (4 and 7 bars across all symbols), plus NON_CONTIGUOUS 459, FIRST_BAR_NOT_0915 241, VOLUME_GAP 156 and OHLC_INCONSISTENT 19. They are excluded and listed per P3.8. |
| Daily vs intraday | intraday range inside the daily bar on 97.1% of sessions; high/low equal within a tick 83.6%; intraday / daily volume median 0.997 |

### Caveats that stay open
- **Range bias:** Upstox is wider on 36% of bars, +1.1 bps on average. P7 must run the pre-registered range-shrink sensitivity, with high and low pulled in by 2 ticks.
- **Survivorship:** only today's 210 F&O names were fetched (`FNO_MEMBERSHIP_CURRENT_LIST`).
- **Corporate actions:** it is not verified whether Upstox daily bars are adjusted. Check against the NSE bhavcopy.
