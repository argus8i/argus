# Volume & Depth Architecture — Red-Team Review

**Author:** Claude · **Date:** 2026-09-11 · **Repo:** `c:\Users\yashw\swing trades`

---

## Headline

**The data is clean. The inference drawn from it is wrong, and it is the same error as last time.**

All four rows of the 11-Sep snapshot reconcile exactly to yesterday's closes under your own inward-truncation rule:

| Name | Prev close | Band | UC calc | UC reported | LC calc | LC reported |
|---|---|---|---|---|---|---|
| CROPSTER | 3.17 | 5% | 3.32 | 3.32 | 3.02 | 3.02 |
| CHANDRIMA | 15.22 | 2% | 15.52 | 15.52 | 14.92 | 14.92 |
| CCDL | 1.38 | 5% | 1.44 | 1.44 | 1.32 | 1.32 |
| GATECH | 0.78 | 5% | 0.81 | 0.81 | 0.75 | 0.75 |

Four for four. The poller works. **Credit where it's due** — and the 8-state `ExecutionState` defaulting to `QUEUED/UNKNOWN` when contra-volume is unmeasured is exactly right. That is the single best design decision in the repo.

Then §2.A concludes:

> *"All 4 watchlist stocks collapsed into Lower Circuits today… proving the absolute liquidity freeze that occurs when circuit runners turn."*

---

## Loophole 1 — "Absolute liquidity freeze" is true for one name out of four

Run the same snapshot through `liquidity_gate.py`:

| Name | Volume | Turnover | Position | Participation | Fill/day | Sessions to clear |
|---|---|---|---|---|---|---|
| CROPSTER | **1,225,000** | ₹36.99L | 12,560 | **1.03%** | 100% | **1.0** |
| CCDL | **545,000** | ₹7.19L | 30,000 | **5.50%** | 100% | **1.0** |
| GATECH | 90,000 | ₹0.67L | 30,000 | 33.3% | 45% | 2.2 |
| CHANDRIMA | **3,134** | ₹0.47L | 4,500 | **143.6%** | 10.4% | **9.6** |

**CROPSTER traded 1.225 million shares today.** That is not a freeze. A 12,560-share position is 1.03% of it and clears in a single session. CCDL traded 545,000 — a 30,000-share position clears in one session too.

**Only CHANDRIMA is frozen**, and it is frozen because it is under PCAS, not because it is at a lower circuit.

This is the *identical* error to the original CROPSTER lockout narrative — reading a locked *price* as an absent *market* — recurring a month later, now with a better data pipeline feeding it. The pipeline upgrade did not fix the reasoning error, it accelerated it.

**Related:** the brief says *"CHANDRIMA trading 3,134 shares with 0 bids."* If 3,134 shares traded, there were bids for 3,134 shares. A trade requires a buyer. "0 bids" is a snapshot of the resting book at one instant, not a property of the session. Same conflation as 25-Aug 10:28.

---

## Loophole 2 — The expansion-ratio denominator is self-defeating

| Name | 2-wk avg | Today | Ratio |
|---|---|---|---|
| CROPSTER | 17,128,000 | 1,225,000 | 0.07× |
| CCDL | 11,976,000 | 545,000 | 0.05× |
| CHANDRIMA | 3,129,000 | 3,134 | 0.00× |
| GATECH | 6,782,000 | 90,000 | 0.01× |

CROPSTER's two-week baseline is **17.1 million shares/day** — an average built *from the distribution days themselves*. A trailing window that contains the pump guarantees that every post-pump session reads as collapse and every pre-pump session reads as normal.

**The metric cannot fire early. It can only confirm what already happened.**

My original spec had the denominator exclude the recent window by construction:

```python
vol_ratio = mean(volume[-20:]) / mean(volume[-80:-20])   # denominator excludes recent
```

The current build is `today / trailing-2-week-mean`, which includes the event. **That is a regression.** Restore the excluded-window baseline and use a **median**, not a mean — one 51-million-share day drags a mean of fourteen beyond usefulness.

---

## Loophole 3 — The snapshot has no timestamp

§2.A says the poller runs *"daily at 08:50 AM and during market hours."* The table carries no time.

A volume figure without a timestamp is uninterpretable, and the expansion ratio compounds it: **intraday cumulative volume divided by a full-day average is a denominator mismatch that manufactures "collapse" at any hour before close.** At 11:00 AM, a perfectly normal session reads as 0.3×.

**Fix, both parts:**
1. Stamp every row with the exchange feed time *and* the local write time (separately — see the staleness issue below).
2. Either compute the ratio only after close, or compare against the **same-time-of-day cumulative** from prior sessions. Anything else is noise dressed as signal.

---

## Loophole 4 — The ceiling gate is ~10× weaker exactly where it is needed

`PRICE_AT_CIRCUIT_CEILING` disqualifies within **1.0% of UC**. In ticks:

| Price | 1 tick | 1% expressed in ticks | Effect |
|---|---|---|---|
| ₹0.75 | 1.33% | **0.8** | gate excludes nothing |
| ₹1.32 | 0.76% | **1.3** | excludes the UC price and one tick |
| ₹3.02 | 0.33% | **3.0** | weak |
| ₹14.92 | 0.07% | 14.9 | meaningful |

The gate is strongest on the stocks that least need it and near-useless on sub-₹5 names — which is the entire watchlist. **Express it in ticks (≥3 ticks below UC) or as a fraction of the band (top 20% of the day's range), never as a percentage of price.** Percentages of price do not scale down; tick grids do.

---

## Loophole 5 — The screener depends on a field the pipeline does not ingest

`accumulation_screener.py` requires *"rising delivery percentage (≥35% and expanding vs 30-day baseline)."*

Ingestion sections A, B and C list: LTP, PrevClose, UC, LC, Band%, volume_shares, turnover_lakh, two_week_avg_volume_shares, volume_expansion_ratio, wap, market_cap_cr, TtlTradgVol, TtlTrfVal, TtlNbOfTxsExctd.

**Delivery percentage appears nowhere.** I flagged it as "not yet captured" on 09-Sep; the screener has since been built to depend on it. It will either throw or be silently skipped — and a silently skipped filter is worse than an absent one, because the screener will report PASS.

This matters more than it sounds: **delivery % is the one wash-trade filter that is expensive to fake**, because shares must actually settle. Volume and spread are both satisfied by a competent wash operation. Delivery is not.

---

## Answering Q1 — Is BSE TTQ + 2-week average an adequate intraday proxy?

**TTQ: yes. The ratio built on top of it: no**, for the two independent reasons above — the denominator includes the event (Loophole 2) and the numerator is a partial day compared against full days (Loophole 3). Both are fixable this week and neither requires new data.

---

## Answering Q2 — the zero-fill threshold (this is the valuable part)

The question as posed — *"resting sell-queue to daily volume"* — has the wrong denominator. You do not race the day's volume; you race **the volume that trades at your price after you join**, against **the quantity ahead of you.** Your own formula states it correctly: `V_cum ≥ R + Q`.

Define **ρ = R / V**, where R is the resting queue when you join and V is the volume remaining for the session:

| ρ | Expectation |
|---|---|
| **≥ 1.0** | no fill this session |
| ~0.5 | partial |
| **≤ 0.3** | full fill |

**Calibrated on your own two hard observations — and they both land:**

```
25-Aug 10:28  resting offer       4,646,100   (from the screenshot)
              avg daily volume,   4,448,328   (your 40,034,951 / 9)
              secondary descent
              rho = 1.04  -> predicts NO FILL.        He got none.  MATCH

27-Aug        Day-3 volume       15,735,454   (your bhavcopy pull)
              rho = 0.30  -> predicts FULL FILL.      Filled 12,560
                                                      in one hour.  MATCH
```

Two independent observations, spanning ρ = 1.04 → no fill and ρ = 0.30 → fast fill, both consistent. **This is a real calibration and it is worth more than the entire two-week-average apparatus.** It uses only numbers you already have.

**The caveat that voids it if ignored:** R is observable at 09:00; **V is not** — V is the rest of the session. You are dividing a known by a forecast. Which is why the **intraday volume curve remains the binding data gap**, still uncaptured, and still the highest-value thing the bridge could log. Bucket `live_depth.json` into 15-minute bins and you can forecast V from the shape of prior sessions. Without it, ρ is an after-the-fact explanation rather than a pre-trade gate.

---

## Answering Q3 — PCAS calibration: the unit is the auction, not the day

Under ESM Stage 2 the session is ~6 discrete call auctions, not a continuous tape.

```
CHANDRIMA daily volume            3,134 shares
auctions per day                      6
effective liquidity unit            522 shares per auction   <-- THE REAL NUMBER
position                          4,500 shares
auctions to clear @100% particip.   8.6  =  1.4 days
auctions to clear @15%  particip.    57  =  9.6 days
price decay while draining (2%/d)          -17.6%
```

**Rule: for PCAS names, divide daily volume by the auction count before any liquidity gate is applied.** A 5%-band continuous name and a 2%-band PCAS name with identical daily volume are not remotely equally liquid, and the current screener cannot tell them apart.

**Second PCAS correction:** in a call auction you match at the equilibrium price or not at all — there is no continuous drip. Fills are **lumpy**. The smooth `daily_fill_fraction` in the drain model is wrong for PCAS and should become a **Bernoulli draw per auction**, not a fraction per day.

---

## Still open from prior reviews, unaddressed

1. **Intraday volume curve** — flagged 10-Sep as the single highest-value pipeline capture. Not built. It is now also the blocker on Q2's ρ gate.
2. **Silent staleness detection** — flagged 11-Sep. `CONNECTED_NO_DEPTH` vs `LIVE_STREAMING` is good and I credit it, but it detects an *empty* book, not a *frozen* one. A backgrounded Chrome tab yields a fully-populated, unchanging depth table with fresh local timestamps. Add: emit `STALE` when LTP and cumulative volume are both unchanged for N seconds during market hours.
3. **Delivery %** — flagged 10-Sep, now a screener dependency (Loophole 5).
4. **Misattributed circulars** — flagged 11-Sep, still standing in `01_MARKET_MECHANICS.md`.

---

## Next steps, in priority order

1. Fix the expansion-ratio denominator (exclude recent window, use median). *One line.*
2. Timestamp every volume row; stop computing intraday ratios against full-day baselines. *One line.*
3. Convert the ceiling gate from % of price to ticks or band-fraction. *One line.*
4. Ingest delivery % or remove the filter from the screener. *Do not leave it silently skipped.*
5. Bucket `live_depth.json` into 15-minute bins. **This unlocks the ρ gate and is the highest-value build remaining.**
6. Divide by auction count for PCAS names; switch PCAS fills to Bernoulli-per-auction.

---

## Consensus stance

**100% cash, unchanged. Zero qualifying paper trades is the correct count** — and worth stating plainly: none of the three historical trades would pass the current gates, which is the system working.

One observation about the process. Four briefs in, every review has found the same class of error: **a number read correctly and then generalised past what it supports.** Today it was 1.2 million traded shares described as an absolute freeze. The engineering keeps improving and the inference keeps outrunning the evidence by exactly one step.

The fix is not more analysis. Before any figure enters a brief as a conclusion, write the sentence that would falsify it and check whether the data rules it out. *"All four are frozen"* is falsified by a single column in your own table.
