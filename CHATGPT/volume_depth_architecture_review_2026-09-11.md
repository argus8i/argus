# ChatGPT review — volume and market-depth architecture revision

**Date:** 11 September 2026  
**Verdict:** **Meaningful P0 progress, but not ready to count qualifying paper sessions. Maintain 100% cash and a zero qualifying-trade count.**

## Confirmed improvements

Direct execution of the revised test suites confirmed:

- `ESM_STAGE_1` aliases now block a new allocation.
- a stock at its upper circuit with nonzero offers no longer returns `BUY`;
- missing surveillance metadata fails closed;
- missing/wide spread, one-sided depth and a narrow daily range do not qualify;
- unknown queue rank/price-specific contra-volume returns `QUEUED` with unknown filled quantity;
- both revised modules compile, and their 9 engine assertions plus 7 screener assertions pass.

These are unit-control improvements. They are not predictive validation, historical validation or evidence of positive expectancy.

## Findings on the 11 September snapshot

The four LTPs equal their reported lower limits at 11:35 IST, so the accurate description is **“trading/last printed at the lower circuit.”** The supplied BSE snapshot does not contain bid depth; it cannot establish that any stock had zero bids or was continuously locked with no counterparty.

The positive reported volumes—1,225,000 CROPSTER, 3,134 CHANDRIMA, 545,000 CCDL and 90,000 GATECH—actually show that some trades occurred before the snapshot. CHANDRIMA displays severe volume contraction relative to the supplied two-week baseline, but this one selected day across four previously identified circuit names does not definitively prove a universal sub-₹10 or post-circuit liquidity law. CHANDRIMA is also above ₹10, so it cannot support a claim specific to sub-₹10 securities.

`volume_expansion_ratio=0.00` for CHANDRIMA is rounding, not zero. Using the displayed inputs, 3,134 / 3,129,000 is approximately **0.001002**, or **0.10%** of the two-week average. Preserve more precision and label the comparison “intraday cumulative volume / full-day historical average” with the snapshot time. It is mechanically time-of-day biased and should not be compared with the 3× daily-volume screener gate before the close unless normalized by an intraday volume curve—which is not yet measured for these stocks.

## Remaining critical loopholes

### 1. The depth bridge still mislabels and lacks a valid observation

The current `shared/live_depth.json` is stale from 10 September 23:03 IST and still says `LIVE_STREAMING` while `active_stock=null`, `stats={}`, and `depth=null`; it predates the code fix and must not be treated as current proof.

The revised JavaScript never populates `active_stock`. When any depth table is open, the logger falls back to the **first watchlist symbol**, so depth from another instrument can be written as CROPSTER. It uses `stats.open` as the logged LTP. `LIVE_STREAMING` requires either bids **or** offers, although the brief says it requires both 5-depth sides. It does not require five levels, known instrument identity, valid stats, or freshness.

The claimed append-only `antigravity/logs/live_depth_ticks.csv` does not exist at inspection time. The CSV writer also replaces missing totals/volume with zero, destroying the distinction between absent parsing and actual zero liquidity. It stores only best quotes/totals, not the five levels, order counts or source sequence needed to reconstruct rank and cancellations. The module docstring still incorrectly says it writes `CHATGPT/observation_log.csv`; it does not.

**Disposition:** bridge remains non-qualifying. Require explicit instrument ID from the same panel as depth, both sides when a two-sided state is claimed, exact ladder payload, source and receive timestamps, schema/selector versions, missing-as-null, CSV-safe serialization, stale/dropout flags, and replay tests against captured DOM fixtures.

### 2. The BSE poller validates bands but not the whole record

The 11 September band arithmetic is internally plausible, an improvement over 10 September. However:

- all four `surveillance` values are `UNKNOWN` and all `group` values are null;
- validation still reports `is_valid=true` because it covers band arithmetic only;
- missing `band_pct` silently defaults to 5%, which is fail-open;
- only a very large UC discrepancy is rejected, while LC discrepancy is not symmetrically checked;
- invalid API bands are replaced by locally calculated bands and published as “effective,” even though an unknown exchange band should block the record rather than be promoted to exchange truth;
- raw responses, HTTP status, endpoint timestamp, parser version and field units are not persisted;
- TTQ and two-week-average unit conversion depends on undocumented string assumptions.

**Disposition:** rename the flag `band_arithmetic_valid`; add a separate record-level `DATA_INVALID` when series/surveillance/date/units are missing; never default a missing band to 5%; keep calculated limits as estimates, not effective exchange limits.

### 3. Volume comparisons have incompatible clocks and denominators

Real-time TTQ is cumulative at the poll time; the two-week baseline appears to be completed full-day volume. Early-day ratios will be systematically depressed. A 20-day screener baseline and a BSE-provided 2-week average are also not interchangeable. PCAS generates stepwise volume at auctions while continuous trading has a different curve.

**Required variables:** observation time, elapsed eligible trading time, session type, number of completed auctions, days in baseline, whether zero/no-trade days are included, split/bonus adjustments, source units, and median as well as mean. Until sufficient intraday history exists, use TTQ descriptively and reserve volume-expansion qualification for EOD.

### 4. The screener remains partially fail-open and band-specific

The screener now enforces spread, but an empty `surveillance_flags` set is interpreted as clean rather than unknown. `DailyCandle.spread_pct` still defaults to favorable 0.5%, so omitted live data can pass. Its near-UC calculation hard-codes a 5% BSE band, creating false decisions for 2%, 10% and 20% names. Its surveillance labels and delivery percentages are supplied inputs without freshness/provenance. The positive test remains synthetic CHANDRIMA-like data.

**Required:** explicit `surveillance_checked_at/source/status`; `spread_pct=None` default; actual daily exchange band; no qualification from empty metadata; real walk-forward fixtures including delisted/failed names. Continue labeling current tests as unit tests only.

### 5. Signal engine mixes entry screening with position management

Because the new-allocation surveillance and series gates run before lower-circuit classification, an already-held XT/ESM position produces `CRITICAL_AVOID` rather than a position-aware exit-review state. The engine has no `position_qty`/intent input, and “HOLD” ambiguously means both “do not enter” and “continue holding.” The LC text still prescribes a 09:00:01 pre-open action even though Q10 and security-session eligibility remain open.

**Required:** separate `ENTRY_ELIGIBILITY` from `POSITION_MANAGEMENT`. Use `NO_ENTRY`, `EXIT_REVIEW`, `ORDER_INELIGIBLE`, and `UNKNOWN_SESSION` rather than overloaded `HOLD`. Never prescribe a time until broker and venue eligibility are verified.

### 6. Queue-state architecture improved, but no fill estimator exists yet

The new `estimate_execution_state()` has the right input names. It still assumes known queue rank and that observed trade volume at the price consumes the queue ahead in simple FIFO order. Modifications, cancellations, hidden/iceberg interest, auction allocation, order resets and the inability to identify which trades occurred after acceptance remain unresolved. Five-depth snapshots cannot normally reveal the user's exact rank.

**Required:** treat states as observed labels where possible. If rank is unavailable, report bounds or unknown. Do not revive `QUEUE_MULT=1`; do not infer zero-bid duration from bhavcopy.

## Disclosure and forensic answer

There is no disclosure that is mechanically triggered merely because a stock first touches lower circuit.

- A Regulation 29 SAST filing can appear if an acquirer/PAC crosses 5%, or if an existing 5%+ holder's acquisition/disposal changes holdings by 2% or more; the filing deadline is generally within two working days, so it need not appear on the LC day. [SEBI Regulation 29 decision text](https://www.sebi.gov.in/web/?file=%2Fsebi_data%2Fattachdocs%2Ffeb-2018%2F1519732348046.pdf)
- Bulk-deal data may identify transactions exceeding the applicable exchange threshold; BSE's current master circular describes the bulk-deal threshold as more than 0.5% of equity shares. That is trade reporting, not proof of promoter/operator intent. [BSE Equity Segment Master Circular](https://www.bseindia.com/markets/MarketInfo/DownloadAttach.aspx?attachedId=8a7fec3a-95bc-4bd2-82de-76e2a84e2915&id=20250429-51)
- Insider/PIT disclosures, promoter encumbrance disclosures, Regulation 30 events, corporate announcements and quarterly shareholding patterns may be relevant, but each has its own trigger and timing. None should be expected automatically on the first LC day.

For every LC onset, search a window—not one day: exchange bulk/block deals and SAST/PIT/encumbrance/corporate filings from at least T−5 through T+3, plus prior preferential allotments, warrants and shareholding changes. Record “no filing located as of [timestamp]” rather than “no sale occurred.” Distinguish filing date, transaction date and exchange dissemination time.

## Next acceptance tests

1. Start the bridge on a deliberately opened depth panel and verify the symbol/code comes from that same panel—not watchlist position.
2. Capture five bid and five offer levels for two different symbols; switch rapidly and prove no cross-symbol join.
3. Close the depth panel: status becomes `CONNECTED_NO_DEPTH`, nulls stay null, no paper observation qualifies.
4. Freeze the DOM/stop updates for more than the freshness threshold: status becomes `STALE`.
5. Break one selector in a fixture: `DATA_INVALID`, not zeros or `LIVE_STREAMING`.
6. Feed missing band percentage and unknown surveillance/group to the poller: record-level invalid, no calculated fallback promoted as official.
7. Compare intraday TTQ at equal clock/auction-session fractions against historical curves; keep EOD expansion separate.
8. Run screener with empty surveillance set, missing spread and actual 2%/10%/20% bands.
9. Run existing-position cases through an independent position-management engine.
10. Reconcile captured totals with EOD bhavcopy while explicitly accepting that snapshots and full-day totals measure different things.

## Consensus stance

**Portfolio: 100% cash—appropriate. Qualifying paper trades: zero—appropriate.** Raw market observations may be logged once provenance and null semantics are correct, but the existing stale/empty depth record and arithmetic-only band validation cannot count. The 60-session/20-fill gate remains necessary, and its clock should begin only after P0 data-quality acceptance tests pass.

No core model was changed in this review. Antigravity should implement; Claude should independently review auction/queue assumptions; ChatGPT should re-run the adversarial suite and audit the disclosure window.
