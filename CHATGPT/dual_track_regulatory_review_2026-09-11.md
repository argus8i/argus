# ChatGPT Regulatory and Model Audit — Dual-Track Architecture

**As of:** 11 September 2026 (IST)  
**Scope:** CCDL evidence, ESM/ST-ASM applicability, Zerodha RMS, observation-log reconciliation, and review of `liquid_momentum_screener.py`  
**Capital state:** 100% cash; observation-only gate remains in force

## Executive verdict

Track 2 is a sensible research expansion because its liquidity floor removes much of Track 1's circuit-lock risk. It is **not yet a deployable model**. The specification must not say that stop-loss execution is guaranteed, and the current screener cannot establish a statistical edge. It is a deterministic filter plus an unvalidated ORB hypothesis.

The Nifty Midcap 150 / Smallcap 250 and `EQ` labels do **not** create a blanket ESM or ASM exemption. The proposed ₹4,000–₹50,000 crore market-cap floor places candidates outside the current ESM inclusion universe (below ₹1,000 crore), which is the correct reason they should not newly enter ESM. Market capitalization is used for ESM inclusion, not exit. Other surveillance measures remain possible.

## 1. CCDL evidence reconciliation

The 14:35 screenshots directly show:

- LTP, open, high and low: ₹1.32; previous close: ₹1.38.
- Displayed bids: zero orders and zero quantity.
- Best displayed offer: ₹1.32, 17,730,606 shares across 925 orders.
- Total displayed offers: 20,538,961 shares.
- Day volume: 760,155 shares.

The same-price stress ratio is `17,730,606 / 760,155 = 23.325`. Total displayed offers divided by day volume is `27.019`. These ratios describe the snapshot; they do not by themselves estimate a probability. At 14:35, immediate executable quantity for a new sell was zero because displayed bid quantity was zero. A statement that future fill probability was exactly 0.0% would require assumptions about later bids, cancellations, price priority, and post-order matched volume.

The screenshot is at 14:35, so “closed locked” should be recorded as “observed locked at 14:35” unless an official close record is separately ingested. The prior 30,000-share CCDL position was already reported closed on 10 September at ₹1.38, so this observation confirms avoided downside/liquidity risk; it does not validate a repeatable exit rule.

## 2. ESM, PCAS and ST-ASM

### ESM / periodic call auction

Current NSE ESM material says the framework applies to companies with market capitalization below ₹1,000 crore. Therefore, a genuinely current ₹4,000 crore minimum market-cap gate keeps the proposed Track 2 universe outside **new ESM inclusion**. This protection comes from market cap—not from `EQ` series or membership in Nifty Midcap 150 / Smallcap 250.

Do not encode `index_member = ESM_exempt`. Also require a dated market-cap field and a daily exchange surveillance-list check. A stock already in ESM does not exit merely because its market cap later rises above ₹1,000 crore.

### Short-term ASM Stage I

For ordinary cash-market stocks, the current NSE FAQ identifies Stage I when any applicable route is met:

1. Five-day close-to-close variation is at least `±25% + beta × Nifty 50 variation`, **and** top-25-client concentration is at least 30% of combined NSE/BSE volume over those five days; or
2. Fifteen-day close-to-close variation is at least `±40% + beta × Nifty 50 variation`, **and** top-25-client concentration is at least 30% over those fifteen days; or
3. One-month high-low variation exceeds 75% and average unique PAN count is below 100 for market cap above ₹100 crore through ₹500 crore, or below 200 for market cap above ₹500 crore.

Stage I raises the applicable margin to 50% or the existing margin, whichever is higher, capped at 100%. ST-ASM Stage II raises it to 100% or existing margin, whichever is higher. ST-ASM Stage II is **not ESM Stage II** and does not itself mean periodic call auction. Securities already in GSM or Trade-for-Trade are excluded from ST-ASM shortlisting because those other controls already apply.

The top-25-client concentration and unique-PAN inputs are not available from ordinary public candles. The model can calculate a public-data warning proxy, but must label definitive ST-ASM eligibility `UNKNOWN` until the exchange publishes the shortlist. A boolean `is_surveillance=False` without source timestamp and unknown-state handling is unsafe.

## 3. Zerodha RMS and charges

- Equity upfront margin is at least 20% of traded value or VaR + ELM, whichever is higher. Zerodha collects full value for CNC delivery and offers up to 5× leverage for eligible MIS equity; the actual symbol-specific requirement can be higher and must come from the current margin calculator/order preview.
- Current Zerodha support lists equity auto-square-off at or after 15:25 for non-CAS stocks and 15:12 for CAS stocks. The time may change with volatility or a broker bulletin. Neither 15:15 nor 15:20 is the current general non-CAS cutoff.
- Zerodha charges ₹50 + 18% GST per dealer/call-and-trade order and per RMS auto-square-off order. Large quantities can be split into multiple orders and charged more than once.
- Equity intraday brokerage is 0.03% or ₹20 per executed order, whichever is lower, plus statutory/exchange charges.

Operational rule: the strategy must schedule its own exit materially before RMS time and must never depend on broker auto-square-off. Broker RMS may act earlier and is not obligated to obtain a fill.

## 4. Track 2 code/model audit

### Blocking defects

1. **Guaranteed stop execution is false.** An SL-Limit order can trigger and remain unfilled if price gaps through its limit or liquidity disappears. A 0.5% offset is a parameter to test, not a guarantee.
2. **Adaptive relaxation changes the strategy after seeing pool size.** Relaxing ATR from 3.5% to 3.0% and institutional holding from 15% to 10% when fewer than 15 names survive creates a second population. It must be a separately named, separately backtested model—not a silent fallback.
3. **Surveillance is fail-open.** `is_surveillance` is only a boolean. Missing/stale data can become “clean.” Required fields are exchange, measure, stage, effective date, checked-at time, source, and an `UNKNOWN` state that rejects the candidate.
4. **Risk sizing can violate its own budget.** The ₹25,000 minimum-notional uplift can increase quantity beyond the ₹1,500 risk cap. Risk cap must dominate minimum notional; otherwise reject the trade.
5. **The ORB signal is not a validated engine.** It checks price above the opening-range high and a volume ratio, but does not verify timestamp boundaries, spread/depth, gap size, slippage, market regime, corporate-action adjustment, or out-of-sample performance. The `atr14_intraday` argument is unused.
6. **Index membership is not represented.** The code screens generic snapshots and does not enforce membership in the stated Nifty universes or preserve membership effective dates.

### Required acceptance gates before prospective scoring

- Freeze one primary ruleset; treat any relaxed version as a distinct experiment.
- Add tri-state, timestamped surveillance ingestion and daily exchange-list reconciliation.
- Add dated Nifty membership, market cap, beta methodology, ATR methodology and corporate-action-adjusted data lineage.
- Enforce `position risk <= risk budget` after every sizing constraint; reject if minimum notional conflicts.
- Model SL-Limit outcomes as `FILLED`, `PARTIAL`, `TRIGGERED_UNFILLED`, and `GAP_THROUGH` with conservative slippage/costs.
- Backtest and then prospectively paper-test with walk-forward splits, delisted/survivorship-safe membership, costs, rejected signals, and realistic order timestamps.
- Do not carry the claimed Sharpe 2.81 into the model until the cited paper, sample, period, costs, and out-of-sample transfer to this exact Indian universe are reproduced.

## 5. Observation-gate status

Before this update, `observation_log.csv` contained one legacy example and three retrospective live records; all four had `counts_toward_paper_gate=false`. This update adds one 11-September CCDL market observation, also ineligible.

- Qualifying prospective paper sessions: **0 / 60**
- Realistically fillable qualifying paper trades: **0 / 20**
- Verified positive net expectancy: **not established**
- Live-capital status: **prohibited under AGENTS.md Rule 1**

## Sources checked

- NSE, Enhanced Surveillance Measure page and FAQ updated 25/28 July 2025.
- NSE, Additional Surveillance Measure FAQ updated 5 September 2025.
- Zerodha Support, equity margin (VaR + ELM) guidance.
- Zerodha Support, auto-square-off timings and call-and-trade charges.
- Zerodha charges schedule.

URLs and retrieval findings were checked on 11 September 2026. Exchange and broker rules remain version-sensitive and must be rechecked before each model release.
