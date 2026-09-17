# Momentum / circuit-risk model v0 — paper protocol

**Status:** Research hypothesis only; observation and paper trading. Not approved for real-money use.  
**Objective:** Identify early, still-tradeable momentum while excluding locked upper-circuit entries and measuring realizable exits.

## Core decision

Do not put actual money behind v0. Run a prospective paper log first. Historical screenshots are useful case studies but cannot estimate win rate, fill rate, or expectancy because the candidate set was selected after the outcome was visible.

## Candidate gate

A symbol enters the research list only when all are true:

1. At least 60 prior trading days of official OHLCV are available.
2. Exact security/series, circuit band, and current GSM/ASM/ESM status are verified.
3. No suspension, periodic-call-auction restriction, or unexplained corporate-action adjustment.
4. Both sides of the continuous-market book are populated and the spread is no more than 1%.
5. This is the first or second abnormal momentum day; never classify a third/fourth locked circuit as an entry.
6. The price is at least ₹10 for v0. This is a provisional microstructure filter, not a claim of fundamental quality.
7. There is actual traded volume, not only displayed queue depth.

Failure of any gate means `NO TRADE / OBSERVE`.

## Features to record

- Return over 1, 3, 5, and 20 sessions.
- Current volume divided by 20-day median volume at the same time of day.
- Number of trades, average trade size, deliverable quantity, and delivery percentage.
- Intraday OHLC/last and distance from the upper circuit.
- Top-five bid/offer quantities, number of orders, spread, and repeated snapshots.
- Changes in queues and executed volume between snapshots.
- Circuit-streak length and fully locked sessions (`open = high = low = close`).
- Market cap/free float, promoter holding/pledge, corporate actions, announcements, and surveillance status.

Displayed bid quantity alone is not a buy signal.

## Paper entry definition

- Observe the opening process; do not infer a signal from a 09:00 snapshot.
- Earliest paper decision: after continuous trading has produced at least 15 minutes of executed data.
- Default evaluation window: 09:30–10:30 IST.
- Paper entry price must be a price at which sufficient opposite-side quantity actually traded after the signal. If locked at UC with no sellers, record `UNFILLED`, not a winning trade.
- Prefer early markup: breakout from a multi-week base with rising genuine volume and a two-sided book. Reject vertical gap/circuit chasing.

The timing parameters are hypotheses to test, not optimized rules.

## Exit policies to compare

- Fixed +5%, +10%, +15%, and +20% take-profit variants.
- First close below the prior close.
- First session that opens away from UC and shows growing offers plus shrinking bids.
- Maximum holding periods of 2, 3, 4, and 5 sessions.
- Immediate attempted exit on circuit-band narrowing, new surveillance restriction, adverse filing, or first lower-circuit indication.

An attempted exit is not an executed exit. Count a fill only when subsequent executed buy volume and queue position make it feasible. Carry unfilled orders forward and mark every additional locked-down session.

## Queue/fill model

For a sell order at the lower circuit:

`estimated_ahead(t) = displayed same-price sell quantity at entry - estimated cancellations ahead - estimated fills ahead`

Because public screenshots cannot identify which cancellations were ahead, report a range rather than a point probability. Required states: `UNSUBMITTED`, `QUEUED`, `PARTIAL`, `FILLED`, `CANCELLED`, `LOCKED_NO_BID`, and `UNKNOWN`.

Never encode “early order = one-hour fill.” Early placement can improve time priority but cannot create demand.

## Validation standard before considering real money

- Minimum 60 trading sessions of prospective scanning.
- At least 30 paper signals and at least 20 realistically fillable entries.
- Every screened candidate logged, including rejections and unfilled orders.
- Walk-forward analysis only; no changing thresholds and re-scoring old outcomes as if changes were known.
- Positive net expectancy after estimated charges, spread, slippage, partial fills, and locked-exit losses.
- Report median and worst-decile return, maximum drawdown, entry/exit fill rates, median/worst time-to-exit, and longest lower-circuit lock.
- A separate holdout period must remain positive. Reject results dependent on one or two outsized winners.

## Current classification

- CROPSTER, CCDL, and GATECH examples: observation/blacklist under provisional price and liquidity gates.
- CHANDRIMA vertical circuit phase: observation only; a locked-UC entry is recorded as unfilled.
- CHANDRIMA's earlier liquid base/markup phase is the appropriate historical period to test using official data.

## Missing inputs

Official historical OHLCV/trades/deliverables, historical surveillance membership, company filings/shareholding, and the user's broker execution log for the CROPSTER exit.
