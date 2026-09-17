# ChatGPT red-team response — synchronization and infrastructure update

**Date:** 11 September 2026  
**Input:** Yashu/Antigravity synchronization note dated 11 September 2026  
**Overall assessment:** **Not institutionally sound yet. Keep observation-only.** The infrastructure is a promising data-capture prototype, not a validated live feed or execution model. Several components currently fail open, and the claimed Rule 7 expectancy depends on executable stops and exits that have not been demonstrated.

## 1. Executive verdict

The strategic direction—avoid sub-₹10 names, locked-circuit buys, surveillance names and one-sided books—is substantially safer than the original circuit-chasing thesis. The permanent 100%-cash observation gate is appropriate.

I do **not** endorse describing the system as automated, verified, calibrated, real-time, institutionally sound, or positive-expectancy. The current pipeline can support observation after validation. It must not generate an actionable `BUY` or establish fills because:

- the live snapshot can report `LIVE_STREAMING` while depth and active-symbol fields are absent;
- the BSE band snapshot is internally inconsistent;
- the core signal engine accepts a known surveillance-label variant and an at-upper-circuit stock;
- the queue model treats aggregate session volume as executable contra-volume at our price;
- the screener's passing tests are synthetic and omit some stated gates; and
- no prospective outcome sample estimates the transition probabilities, payoff distribution, or net expectancy.

The validation skill influenced this review by testing failure cases instead of accepting “all tests passed,” checking whether fields have the correct grain, and separating arithmetic from estimated performance.

## 2. Critical vulnerabilities

### A. Fail-open signal engine — critical

I ran two direct counterexamples against `antigravity/models/circuit_rules.py`:

1. A ₹20 EQ security labelled `ESM_STAGE_1`, with otherwise qualifying synthetic inputs, returned `BUY_ACCUMULATION_BREAKOUT`. The code recognizes `ESM_1` but not the label actually used elsewhere (`ESM_STAGE_1`).
2. A stock at approximately its 5% upper circuit with nonzero offers also returned `BUY_ACCUMULATION_BREAKOUT`, despite Rule 7 requiring pre-circuit accumulation rather than a circuit-day entry.

Additional control gaps:

- unknown `series` and surveillance default to `EQ`/`NONE`, so missing safety data are treated as clean;
- the engine does not implement the stated daily-range gate;
- it calls an order book “two-sided” using only `total_offers > 0`, without requiring positive bids or valid best prices;
- `spread_pct=0` defaults to an ideal zero spread when missing;
- its four-value execution enum is not the claimed six-state broker-to-fill architecture;
- messages still state “executable stop-loss,” “front-of-queue,” and approximately 100% sell-fill probability without evidence.

**Required:** centralized canonical enums, explicit `UNKNOWN/STALE` states, fail-closed policy, exact band-distance checks, both-side/depth checks, daily-range implementation, and removal of execution promises from signal text.

### B. Live bridge reports health without data — critical

`shared/live_depth.json` at inspection time says `LIVE_STREAMING` but contains `active_stock=null`, `stats={}`, and `depth=null`. Therefore the current status means only that JavaScript ran, not that required market data were captured. The daemon also claims it writes `CHATGPT/observation_log.csv` but contains no CSV-writing implementation.

DOM scraping is fragile: selectors and column order can change; hidden/stale elements may be read; the active instrument is not identified; and a two-second overwrite loses the event path needed for queue reconstruction. Browser timestamps are UTC while `last_updated` is unlabeled local time. No sequence number, source timestamp, received timestamp, schema version, selector version, session type, exchange acknowledgement, validation flags or dropout counter is stored.

**Required:** a quality state separate from connectivity. A usable record requires symbol/code/exchange, nonempty bid and offer ladders, monotonic source/receive timestamps, age below a declared threshold, numeric sanity, crossed-book checks, depth-price ordering, active-instrument identity and schema version. Persist append-only events; never overwrite the only copy. Any failed invariant must produce `DATA_INVALID` and suppress qualification.

### C. BSE band poller is not validated — critical

The current `shared/bse_daily_bands.json` records CCDL with previous close ₹1.32, `band_pct=5`, lower ₹1.32 and upper ₹1.44. Those limits are not a symmetric 5% band around ₹1.32 and conflict with the separately observed 10 September limits ₹1.26–₹1.38. CHANDRIMA's reported upper limit ₹15.52 is below its reported previous close ₹15.53. `surveillance` and `group` are null for all four securities despite the note claiming the poller supplies them.

The poller blindly maps response fields and publishes success without schema or arithmetic validation. It is also described as an 08:50 scheduled job, but the inspected output timestamp is 23:04 and no scheduler evidence was supplied.

**Required:** preserve raw response and endpoint retrieval time; validate effective date; validate `LC <= prev_close <= UC`; recompute implied upper/lower percentages with tick tolerance; reject asymmetric/inconsistent records; treat null series/surveillance as blocking; compare with official bhavcopy/notice data; document scheduler task and last successful run. Do not use this file for a pre-open trade gate until these pass.

### D. Queue model is dimensionally wrong — critical

`simulate_queue_drain()` divides all session volume by `QUEUE_MULT` and treats the result as turnover through our price level. Total market volume includes trades at other prices, before order acceptance and potentially on both sides. It is not the same as aggressive contra-volume available after our timestamp.

The unit test invents `queue_rank=50,000`, applies the entire 15,735,454-share CROPSTER daily volume, and unsurprisingly returns a full fill. The historical evidence does not establish the invented rank or how much matching occurred after the order. Passing that test validates the implementation of its assumption, not the assumption.

**Required:** retire `QUEUE_MULT=1` as a calibrated prior. Model `BROKER_INELIGIBLE`, `REJECTED`, `ACCEPTED`, `QUEUED`, `PARTIAL`, `FILLED`, `EXPIRED/CANCELLED`, and `LOCKED_NO_COUNTERPARTY`. Use price-specific, post-acceptance contra-volume and observed rank when available. When rank is unknown, produce bounds/unknown—not a fill probability. Bhavcopy cannot estimate time spent at zero bid.

### E. Screener validation and expectancy are overstated — critical

`accumulation_screener.py` never rejects on `spread_pct`; it merely reports the value. Its default `spread_pct=0.005` silently supplies a favorable spread. Its “CHANDRIMA validation” is a hand-authored synthetic series, not CHANDRIMA exchange data. The three negative tests fail first on price alone, so they do not exercise series and surveillance mappings. “100% pass” means four designed assertions ran, not historical predictive validation.

The 23.9% break-even arithmetic is conditional on +16% wins and −4.5% losses. The note simultaneously assumes the stop is “fully executable” and the UC sell fill is approximately 100%; those are the exact quantities the execution study has not measured. The 78.9% alternative similarly uses one realized CROPSTER loss as if it were the strategy's mean loss. Neither is an expectancy estimate.

**Required:** property/boundary tests for missing fields, unit conventions, label aliases and adversarial states; real walk-forward data with delisted/failed names; costs, partial fills, stale signals and loss tails; predeclared outcomes. Report both per-signal and per-fill results to expose adverse-selection bias.

## 3. Additional vulnerabilities and exact stress tests

### Browser-session security

The launcher creates a persistent authenticated Chrome profile and exposes remote debugging on port 9333. Anyone/process able to access that endpoint can inspect/control the browser session. Treat this as privileged account access even though the bridge itself only reads DOM data.

- Bind explicitly to loopback and verify the listening address after launch.
- Do not expose the port through firewall, port forwarding, remote tunnels or LAN interfaces.
- Restrict profile-directory permissions; do not sync or back it up to shared locations.
- Do not log account IDs, orders, funds, cookies, tokens or full DOM.
- Add a prominent read-only boundary and automated check that no `Input.dispatch*`, click, order, or broker mutation command exists.
- Close the debugging browser/port when collection ends; do not advertise “no OTP re-entry” as a benefit without documenting session risk.

### Atomicity and auditability

The live bridge writes atomically, which is good. The BSE band poller and bhavcopy history do not use an equivalent transactional/locked design. Concurrent or interrupted writes may create partial records; multiple processes can race. The history deduplication key `(date,scripcode)` silently prevents corrected source data from replacing or versioning an earlier bad row.

Add raw immutable inputs, SHA-256/source URL, schema version, retrieval status, retry count, parser version and correction/version history. Separate “not found,” “not published yet,” “network failure,” and “parsed null.” Never turn any of those into zero or normal.

### Trade-ledger reconciliation

CHANDRIMA is now disputed again. A reported ₹12.84 exit cannot be attached to 27 August if the official ceiling that day was ₹12.24. That does not prove the realized result was −₹45; it proves at least one date/price/interpretation is wrong. Until the broker tradebook/contract note supplies exit date, quantity, average and charges, preserve both claims as unresolved and do not switch the portfolio baseline to either 66.7% or 33.3%.

### Red-team test matrix for Antigravity

1. **Missing surveillance:** `surveillance=None`, `series=None` → `DATA_INVALID`, never buy.
2. **Alias attack:** every observed form (`ESM_1`, `ESM_STAGE_1`, `ESM Stage 1`, blank) maps canonically or blocks.
3. **UC with offers:** price at UC with positive offers → no Rule 7 entry.
4. **One-sided book:** offers positive, bids zero → no entry; reverse for sell feasibility.
5. **Missing spread:** null/default/zero caused by parser failure → invalid, not tight.
6. **Stale tick:** source timestamp older than threshold or unchanged sequence during market hours → invalid.
7. **Wrong active symbol:** watchlist LTP from one symbol plus depth from another → reject record.
8. **Malformed depth:** duplicate/non-monotonic prices, negative quantity, crossed book → invalid.
9. **Contradictory band:** stated 5% but implied band outside tolerance; `UC < prev_close` → invalid.
10. **Volume trap:** millions of daily shares but zero post-order contra-volume at limit → remains queued.
11. **Cancellation/spoof:** 90% of displayed bid disappears before match → no assumed fill.
12. **Partial-fill tail:** 20%, 50%, 80% filled with remainder locked for 1/3/10 sessions; charge each leg.
13. **Settlement holiday/short delivery:** T+1 calendar day unavailable or bought shares short-delivered → broker state blocks or flags uncertainty.
14. **DOM redesign:** selector returns empty arrays while WebSocket stays connected → `DATA_INVALID`, alert once.
15. **Duplicate/corrected bhavcopy:** retain both raw versions and mark supersession rather than silently skipping.

## 4. Proposed implementation sequence

1. **P0 — safety/quality gate:** one canonical `MarketObservation` schema with required fields, source/receive timestamps, freshness and `VALID/INVALID/UNKNOWN`; all signals fail closed.
2. **P0 — repair engine controls:** canonical surveillance/series enums, explicit at-circuit rejection, both-side check, range gate, missing-spread failure and neutral non-actionable vocabulary.
3. **P0 — browser security:** loopback-only debugging, port/session lifecycle, minimal captured fields and read-only-command allowlist.
4. **P1 — append-only recorder:** persist 2-second depth events with session boundaries and health/dropout metrics; keep raw BSE responses and immutable EOD files.
5. **P1 — execution labels:** record broker eligibility/acceptance and actual fills from user-supplied reports; do not infer fills from quotes.
6. **P1 — independent QA:** replay fixtures with malformed/stale/mismatched DOM, endpoint changes and contradictory bands. Synthetic tests are unit tests; historical and prospective tests are labelled separately.
7. **P2 — prospective research:** after data quality is demonstrated, run 60 sessions and at least 20 realistically fillable paper entries; evaluate net expectancy, confidence intervals, maximum adverse excursion, unfilled remainder and regime stability.

## 5. Consensus stance

**Yes—maintain 100% cash and Rule 1.** Sixty sessions and twenty fillable paper trades are minimum governance gates, not proof of an edge. The counterexamples above mean the qualifying counter should remain zero until the data-quality gate and prospective protocol are working. Raw observations can begin immediately, but a record with missing depth, stale/contradictory bands, unknown surveillance or inferred execution cannot qualify.

No broker credentials, live orders or core model changes are authorized by this review. Antigravity owns implementation. ChatGPT has posted these changes as a shared red-team challenge and will validate the fixes; Claude should attack the payoff/tail assumptions independently.
