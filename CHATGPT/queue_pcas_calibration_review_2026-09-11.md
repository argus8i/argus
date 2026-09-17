# ChatGPT review — queue ratio and PCAS calibration

**Date:** 11 September 2026  
**Assessment:** **Needs revision.** The modules execute, but the queue thresholds and PCAS clearance/decay claims are not empirically calibrated. Keep them as experimental scaffolding only.

## Calculation checks

- CROPSTER 12,560 / 1,483,000 = **0.8469%**, reasonably described as approximately 1% of daily volume. That ratio does not prove one-session clearance.
- CCDL 30,000 / 563,000 = **5.3286%**. That ratio also does not prove one-session clearance.
- CHANDRIMA 4,500 / 3,134 = **143.5865%** of that day's traded volume. This prevents a same-day full fill if the order may consume no more than the observed total, but it does not mean zero fill or “truly frozen.”
- The PCAS arithmetic under its assumptions is reproducible: 522.33 shares per equal sixth, 78.35 at 15%, 57.43 auctions, 9.57 **trading days**, and −17.58% after 9.57 exact −2% days. The assumptions—not the arithmetic—are unsupported.
- All named scripts executed. However, `CHATGPT/audit_checks_2026_09_09.py` is a diagnostic collector, not a 27-assertion test suite. Its successful exit coexists with TypeErrors and outstanding defects. “27/27 pass” must be withdrawn.

## 1. Queue model: use deterministic bounds, not linear or exponential probability

The two claimed hard anchors are not comparable:

- The 25-Aug value 4,646,100 was a displayed sell quantity at one snapshot, not demonstrated as the user's exact quantity ahead after exchange acceptance. The supplied 4,448,328 volume also conflicts with the project's earlier 25-Aug EOD total of 3,913,358 and must be sourced/defined.
- The 27-Aug calculation reuses the **25-Aug** 4,646,100 queue as `R`. No 27-Aug rank observation supports that substitution. Full-day 15,735,454 is not price-specific post-acceptance contra-volume, and the user reports filling in one hour—not at the end of that day.

Therefore `rho=0.30` was constructed to fit the success; it is not a measured second anchor. Thresholds 0.30/1.00 are not calibrated.

If `R` is known and fixed and `V` means cumulative executable contra-volume at the same price after acceptance, FIFO gives a deterministic accounting rule:

```
filled_qty = min(Q, max(0, V - R))
```

It does not produce a fill “probability.” Probability is needed only because future `V`, true `R`, cancellations, priority changes and eligibility are uncertain. Neither a linear interpolation nor `exp(-lambda*rho)` is justified without repeated labelled observations. An exponential curve is especially unsuitable as written because it gives positive probability at arbitrarily large rho and less than 100% at rho=0 unless amended.

Recommended model:

1. **Observed execution layer:** deterministic states from broker/exchange acknowledgements and actual partial fills.
2. **Forecast layer:** empirical survival/time-to-fill model with censoring, preferably discrete-time hazard by 15-minute interval or auction. Features: price-specific visible rank/bounds, order age, remaining quantity, contra-volume since acceptance, cancellations/queue changes, session type, band, spread, time of day, surveillance state and prior no-trade auctions.
3. Until enough cases exist, return scenario bounds (`best/base/stress`) and `UNESTIMATED`, not 0%/100% probabilities.

The imported U-curve is also not “calibrated” to these securities. It came from studies of different populations and the hard-coded CDF values are not traceable estimates from those papers. Use it only as an explicit sensitivity curve. Forecast price-specific contra-volume, not total daily market volume.

## 2. PCAS model: auction granularity is right; clearance engine is not

Moving from smooth continuous trading to auction-by-auction states is directionally correct. The implementation nevertheless does not simulate what its description claims:

- `daily_volume / 6` assumes equal volume in all auctions. PCAS volumes can be zero and highly uneven.
- 15% is a project participation cap, not an exchange “allowed per auction” quantity.
- `simulate_bernoulli_auction_fill()` contains no Bernoulli draw; it is deterministic FIFO subtraction and can produce a partial fill.
- It assumes all matched auction volume is relevant contra-volume at the order's equilibrium price and ignores eligibility and price compatibility.
- It assumes the same daily volume repeats for 9.57 days.
- `sessions_to_clear` is actually **trading days** after dividing auctions by six. Calling both an auction and a day “session” creates a unit error.
- Applying −2% for a fractional 9.57 days assumes uninterrupted LC closes and prices all 4,500 shares as though realized at the final horizon. A liquidation should calculate cash-flow-weighted fills per auction/day; earlier partial fills suffer less decay, and some paths do not decline each day.
- `is_clearable` is a policy screen under assumed volume/participation, not an estimated ability to exit.

Recommended auction state transition for each auction `a`:

```
eligible_a -> accepted_before_cutoff_a -> price_compatible_a
-> equilibrium_contra_volume_a -> queue_a -> fill_a -> remaining_a
```

Draw or replay the full vector of per-auction volumes rather than dividing by six. Include zero-match auctions, cancellations/modifications, price changes, remaining quantity and expiry/carryover rules. Estimate distributions only after collecting PCAS histories.

## 3. Random closure in the 44th–45th minute

Random closure should not be represented as extra fill probability. It is an **order-management deadline risk**:

- For a paper order already accepted well before minute 44, random closure no longer affects acceptance; it freezes further modification/cancellation and the order enters that auction's price discovery subject to its price and priority.
- For an order submitted or modified during the random-close interval, model a stochastic acceptance indicator based on submission timestamp/latency versus the unknown cutoff. With a uniform cutoff only if the exchange rule supports that assumption, acceptance probability within the one-minute window is the remaining fraction of the window. Otherwise leave its distribution unspecified.
- Store local submit, broker accept, exchange acknowledge and auction cutoff/session ID separately. A browser click timestamp is not an exchange timestamp.
- Operational research rule: qualify only paper orders acknowledged before the random-close window; late submissions are labelled `CUTOFF_UNCERTAIN`, not assumed accepted.

Do not assume pre-market 09:00 priority applies to PCAS. PCAS auction timing and carryover rules must be versioned from the applicable exchange circular.

## 4. T−1 delivery percentage

Using delivery data available through **T−1** for an intraday decision on T is methodologically sound as a lagged predictor and avoids look-ahead bias. It is not proof of current-day accumulation.

Implementation requirements:

- Feature name must encode the cutoff, e.g. `delivery_median_tminus1_10d` and `delivery_expansion_tminus1`.
- At decision time, record the latest publication date, source, retrieval timestamp and any settlement holiday/staleness.
- Never backfill T delivery data into a T signal during later evaluation.
- Missing, revised or stale files fail closed; corporate actions and identifier changes need adjustment.
- Validate whether delivery data exist and are comparable for BSE/NSE, EQ/T2T and PCAS populations.
- Test several lag windows and thresholds only in training; freeze the chosen rule before out-of-sample evaluation to avoid selection bias.

Yesterday's delivery trend may be combined with current verified price/volume/depth observations, but the model must label it historical. Current intraday TTQ should not be compared directly with a full-day delivery/volume baseline without time-of-day normalization learned for the relevant venue/session type.

## 5. Additional code issues

- The proximity refactor uses a universal ₹0.01 tick. Tick size must be a dated exchange/security field. Since Rule 2 already excludes sub-₹10 names, the claim that this refactor solves sub-₹10 distortion is irrelevant to qualifying entries.
- A 15% band-fraction ceiling is an unvalidated policy threshold. Treat it as a tunable hypothesis and test sensitivity; do not call it optimal.
- The recent-20/base-60 volume ratio excludes the latest 20 sessions from its denominator, but it is “uncontaminated” only if the abnormal run began within those latest 20 sessions. Earlier leakage remains possible.
- A volume share below 15% cannot establish clearance. Total daily volume includes trades before acceptance, other price levels and non-contra transactions relative to the order.

## Decision

- Queue model thresholds: **reject as calibrated; neither linear nor exponential is presently supported.**
- PCAS engine: **retain auction-state skeleton, reject equal-sixth/15%-clearance and −17.6% as forecasts.**
- T−1 delivery gate: **approve as a lagged research feature with strict timestamping; not as same-day confirmation.**
- Liquidity conclusion: **no watchlist stock is proven “frozen” solely by position/daily-volume ratios.** CHANDRIMA is demonstrably too large for a full fill relative to that day's total, but partial fill and future-day outcomes remain unknown.
- Governance: portfolio stays 100% cash; qualifying paper count remains zero until prospective telemetry and outcomes are valid.

The validation framework influenced this review by distinguishing reproducible arithmetic from identified parameters, treating censored execution correctly, and checking whether a passing script actually contains passing assertions.
