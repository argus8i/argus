# Screener Spec — "catch it before the circuit phase"

**Author:** Claude · **Version:** 1.0 · **Date:** 2026-09-09
**Build target:** Antigravity. This is a spec, not code — build it, then post results in `antigravity/PROGRESS.md`.

---

## What this screener is for, and what it is not for

**Not for:** finding stocks that are locked at upper circuit today. Those are trivially findable and, per `fill_model.py`, unbuyable — 49% of attempts fill, 65% of fills lose, average win +6.8% against average loss −19.9%.

**For:** finding stocks in **accumulation or early markup** — the phase before the locks start, where the book is two-sided, a stop-loss actually executes, and a position can be entered at a chosen price and exited at a chosen price.

CHANDRIMA's tradeable phase was mid-July to 20 August: **6.50 → 8.00, +23% over four weeks**, fully liquid throughout. The +83% vertical that followed was unreachable in both directions. The screener targets the first phase.

---

## Data sources (all free, no API key, no login)

| What | Where | Notes |
|---|---|---|
| Daily OHLCV + **delivery %** | NSE `sec_bhavdata_full_DDMMYYYY.csv` | The delivery column is the important one and most screeners ignore it |
| BSE daily bhavcopy | BSE "Equity Bhavcopy" ZIP | Needed — four of five watchlist names are BSE-listed |
| GSM list | NSE / BSE surveillance pages | **Refresh daily.** A name appearing here is an instant disqualify |
| ASM list (long + short term) | NSE / BSE surveillance pages | Same |
| ESM list | NSE / BSE surveillance pages | Same — and this is the framework most likely to catch this universe |
| Series code | in the bhavcopy | `EQ` = fine. `BE` / `T2T` = disqualify |

Pull the last **120 sessions** of bhavcopy so the 60-day base and 20-day volume windows have room.

---

## Filters

### Stage 1 — hard disqualifiers (drop immediately, no scoring)

```python
close < 10.00                                  # the price floor. Non-negotiable.
series not in ("EQ",)                          # BE / T2T are already flagged
symbol in gsm_list | asm_list | esm_list       # exchange has noticed
any(high == low for last 5 sessions)           # circuit-locked in the last week
median(high - low) / close < 0.03  over 5d     # no real intraday range = no liquidity
turnover_20d_avg < 50_00_000                   # < ₹50 lakh/day: can't get out
```

The `high == low` test is the cheapest and most powerful line in the whole screener. A day where high equals low is a locked day. **Any lock in the last 5 sessions disqualifies the name** — that is the phase we are explicitly avoiding.

### Stage 2 — the setup

```python
base_60d      = median(close[-65:-5])
run_pct       = close / base_60d - 1
0.15 <= run_pct <= 0.40                        # started moving, not yet vertical

vol_ratio     = mean(volume[-20:]) / mean(volume[-80:-20])
vol_ratio >= 3.0                               # accumulation shows up as volume first

deliv_now     = mean(delivery_pct[-10:])
deliv_before  = mean(delivery_pct[-40:-10])
deliv_now > deliv_before                       # rising delivery = shares changing hands
deliv_now >= 0.35                              # not pure intraday churn
```

**Why `run_pct` is capped at 40%:** above that, the markup phase is over and the circuit phase has begun. The screener is deliberately built to *miss* the exciting part. That is the point.

**Why delivery percentage matters:** it separates real accumulation from wash-trading volume. Volume can be manufactured by trading with yourself; delivery is harder to fake because shares must actually settle. Rising delivery alongside rising volume is the honest version of accumulation.

### Stage 3 — score and rank

Rank survivors by:

```
score = 0.40 * z(vol_ratio)
      + 0.30 * z(deliv_now - deliv_before)
      + 0.20 * z(1 / spread_pct)          # tighter spread ranks higher
      + 0.10 * z(-abs(run_pct - 0.25))    # prefer the middle of the 15-40% window
```

Output the top 10 as a table. Ten is enough — this is a one-position-at-a-time strategy (Rulebook Rule 3).

---

## Required output columns

| Column | Why it's there |
|---|---|
| symbol, exchange, series | identity |
| close, 1-tick % of price | the tick tax, made visible |
| run_pct vs 60d base | where in the cycle |
| vol_ratio | accumulation signal |
| delivery % now vs before | the honesty check |
| spread % | can you actually get out |
| locked days in last 20 | history of illiquidity |
| **UC / LC today, and yesterday's** | **band-revision detector — see below** |
| GSM / ASM / ESM flags | should all be empty by construction; print anyway as a check |

---

## The daily band-revision monitor (build this first)

Separate from the screener, and **higher priority than the screener itself.**

For every name in `shared/02_WATCHLIST.md`, every morning:

```
if UC_today / prev_close  <  UC_yesterday / prev_close_yesterday:
    ALERT: band narrowed on <symbol>
```

CHANDRIMA's band went 20% → 10% on 27 Aug with no announcement anywhere in the broker app. Rulebook Rule 4 makes a band narrowing the **highest-priority exit trigger**, and it is currently detectable only by manually comparing two numbers each morning. Automating this is a few lines and is worth more than the entire screener.

---

## Validation before this is trusted

Run it on history and check it against the names we already know:

1. **CHANDRIMA:** does it flag between ~15 July and ~20 August (the tradeable window)? It **must**. Does it stay silent from 22 Aug onward (the vertical)? It **must** — flagging there is a false positive of the exact kind that lost money.
2. **CROPSTER:** does it stay silent throughout? Every phase of CROPSTER should fail either the ₹10 floor or the lock test. If any date passes, the filters are too loose.
3. **CCDL / GATECH:** must never appear. Both fail the price floor by 8× and 12×.

**A screener that fires on CROPSTER or CCDL is broken, regardless of how good its backtest looks.**

---

## Honest limitation

This screener finds *momentum with liquidity*. It does not detect manipulation, and it should not try to. It cannot distinguish an operator quietly accumulating from a genuine re-rating on real news — and it does not need to, because the entry conditions (two-sided book, working stop, ≥₹10, no locks) make both cases survivable.

**The edge here is not prediction. It is only ever taking positions you can get out of.** Everything above is machinery for enforcing that one thing.
