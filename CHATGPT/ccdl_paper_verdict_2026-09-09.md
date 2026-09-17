# CCDL paper verdict — 09 September 2026

**Instrument assumed:** CCDL on BSE (user said “CCDA”)  
**Evidence cutoff:** 13:50 IST screenshot, price ₹1.32, previous close ₹1.26  
**Verdict:** Speculative paper entry was executable at ₹1.32 at the quoted instant, but the setup is late-cycle/crowded and fails the v0 quality gates. Paper-test only; no real-money approval.

## Critical microstructure correction

At 13:50 the visible book showed:

- Best bid ₹1.31: 6,56,61,799 shares.
- Best offer ₹1.32: 4,10,174 shares.
- Total bids: 9,07,92,858 shares.
- Total offers: 4,10,174 shares.
- Cumulative volume: 2,00,77,573 shares.

Therefore CCDL was not a zero-offer locked UC at that instant. A buy limit at ₹1.32 was marketable against the displayed ₹1.32 offers. For a notional below ₹1 lakh (about 75,700 shares at ₹1.32), displayed best-offer quantity was more than five times the proposed quantity, subject to latency, cancellations, broker controls, and quote freshness.

The ratio `offer quantity / bid quantity = 0.45%` is an imbalance statistic, not a fill probability for a marketable buy. Price-time priority matters when joining a resting queue; it does not require a buyer crossing the ask to wait behind lower-priced bids.

## What the two snapshots imply

From 09:51 to 13:50:

- Volume increased by about 1.33 crore shares.
- Total displayed bids increased by about 61 lakh.
- Displayed offers fell from about 21.02 lakh to 4.10 lakh.
- Best-bid quantity at ₹1.31 increased by about 2.29 crore.

Bullish interpretation: genuine demand absorbed supply while the bid base strengthened, leaving relatively little stock offered at the upper price.

Bearish interpretation: the crowd was already extremely concentrated on the buy side while informed or early holders could distribute into it. The existence of 4.10 lakh offers at the upper price means the lock was not absolute. A single snapshot cannot distinguish accumulation from distribution.

## Exact paper decision

### If the decision had to be made at 13:50

- **Paper action:** one speculative paper entry at ₹1.32 using a limit order, recorded as filled only up to subsequently verified executed quantity.
- **Confidence:** low.
- **Classification:** late momentum experiment, not a validated setup.
- **No averaging down and no second entry.**

This exception is useful for testing the user's hypothesis; it is not compatible with the conservative v0 gate that excludes sub-₹10 securities and late circuit chasing.

### Profit paths to test from ₹1.32

Indicative percentage levels, before exchange rounding and actual daily bands:

| Return | Indicative price |
|---:|---:|
| +5% | ₹1.386 (approximately ₹1.39) |
| +10% | ₹1.452 (approximately ₹1.45) |
| +15% | ₹1.518 (approximately ₹1.52) |
| +20% | ₹1.584 (approximately ₹1.58) |

Because a ₹0.01 tick equals about 0.76% at ₹1.32, exact attainable circuit and target prices must use the exchange-published band; ordinary percentage rounding is insufficient.

## Paper exit policy

Compare four pre-registered variants rather than choosing after the path is known:

1. **One-circuit exit:** attempt to sell at the first attainable price around +5%.
2. **Two-circuit exit:** target approximately +10%.
3. **Three-circuit exit:** target approximately +15%.
4. **Four-circuit exit:** target approximately +20%.

For all variants, an earlier attempted exit overrides the target if any of these occurs:

- Next session does not make a new high during the first 30 minutes of continuous trading.
- Offer quantity expands materially while bid quantity contracts across at least three timestamped observations.
- Price trades below the prior session close after the opening phase.
- Circuit band narrows, series/surveillance status worsens, or an adverse/unexplained filing appears.
- High volume produces no price progress (churn/distribution warning).

An attempted exit is not booked as an executed exit until the book and subsequent prints support a fill.

## Downside paths

From a ₹1.32 paper entry, indicative sequential 5% declines are approximately:

| Locked-down sessions | Indicative price | Loss |
|---:|---:|---:|
| 1 | ₹1.25 | −5% |
| 2 | ₹1.19 | −9.75% |
| 3 | ₹1.13 | −14.26% |
| 5 | ₹1.02 | −22.62% |

Actual quoted levels depend on tick rounding and exchange bands. The operational worst case is not the first −5%; it is an unfilled sell order carried through multiple lower circuits.

## Expected path — qualitative, not statistical

The order book supports two near-term branches:

- **Continuation:** available offers are absorbed; the next base/circuit shifts upward and the paper trade reaches the first target.
- **Distribution/break:** sellers use the very large crowd bid as exit liquidity; offers expand, the price fails to progress, and the stock reverses.

There is not enough prospective evidence to assign honest numerical probabilities. Any exact win probability would be fabricated.

## Information required before any real-money reconsideration

- Exact BSE security code, ISIN, series, and current circuit band.
- Current ASM/GSM/ESM and periodic-call-auction status.
- Official intraday time-and-sales, not only cumulative volume.
- At least 60 sessions of OHLCV, trade count, and deliverable quantity.
- Latest filings, shareholding, promoter pledge, corporate actions, and bulk/block deals.
- Prospective results from the same frozen signal across many candidates.

## Bottom line

At the quoted 13:50 instant, ₹1.32 was an executable paper-entry price for a small notional because actual offers were displayed. The bullish fact was absorption plus a growing bid base. The bearish fact was extreme crowding, penny-level tick size, and substantial turnover at the top. The calculated-risk test is therefore a one-shot paper position with independently scored +5/+10/+15/+20 exit variants—not a real-money buy and not an assumption that the rally must last three or four more sessions.
