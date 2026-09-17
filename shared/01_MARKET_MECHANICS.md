# Market Mechanics — shared ground truth

**Maintainer:** Claude · **Version:** 1.0 · **Date:** 2026-09-09
**All three assistants read this before proposing anything. If you find an error, fix it and note the change at the bottom.**

This file exists so that Antigravity, Claude and ChatGPT are not each re-deriving the plumbing from scratch — and not each getting it subtly wrong.

---

## 1. Circuit limits

Every stock has a daily price band: 20%, 10%, 5%, or 2%. The band is computed off the **previous close**, not the open.

- **Upper circuit (UC)** — the highest permitted price today. At UC, buyers queue and sellers are absent.
- **Lower circuit (LC)** — the lowest permitted price today. At LC, sellers queue and buyers are absent.
- **"Locked"** — the stock is at UC or LC and one side of the book is empty. No trading occurs.

### Inward Tick Truncation (Sub-₹10 Price Bands)
Per **BSE Consolidated Master Circular Equity Segment Item 1.6**:
- A lower price-band cannot be specified as negative, flooring the lower band to one tick (**₹0.01**).
- For sub-₹10 securities, price bands are **truncated inward** to the tick size rather than rounded to the nearest tick.
  - Example: A ₹1.32 previous close with a 5% band gives raw bounds:
    - Raw UC: $1.32 \times 1.05 = 1.3860 \to$ truncated inward to **₹1.38** (NOT ₹1.39).
    - Raw LC: $1.32 \times 0.95 = 1.2540 \to$ rounded inward up to **₹1.26**.

**The exchange can revise a band mid-run without any announcement in your broker app.** CHANDRIMA went from a 20% band (26 Aug) to a 10% band (27 Aug) with no notice other than the changed UC/LC numbers.

> **Check UC and LC every morning and compare to yesterday. A narrowing band is the earliest exit signal available and it is free.**

---

## 2. Why you cannot buy at the upper circuit

Order matching is strict **price-time priority**. At UC everyone bids the same price, so only time matters — and you are last.

Displayed imbalance ratio ≈ `total_offer_qty / total_bid_qty`. Measured from our own screenshots:

| Stock | Date/time | Offer qty | Bid qty | Imbalance ratio |
|---|---|---|---|---|
| CHANDRIMA | 26 Aug 15:34 | **0** | 16,97,766 | **0% (Locked)** |
| CHANDRIMA | 28 Aug 09:17 | **0** | 1,45,89,474 | **0% (Locked)** |
| CCDL | 09 Sep 13:50 | 4,10,174 | 9,07,92,858 | **0.45% (Two-sided)** |

*Note: At 13:50, CCDL had an active offer of 4.10L shares at ₹1.32, so a limit buy at ₹1.32 was marketable against resting offers rather than queuing behind 9.07 Cr ₹1.31 bids. However, the presence of sellers in a circuit run is adverse information (distribution).*

**Corollary — the most important sentence in this folder:** you only get filled when someone sells to you, and in a locked-up stock the only people selling are people who have decided to leave. *A fill is adverse information.*

---

## 3. Why you cannot sell at the lower circuit

CROPSTER, 25 Aug 2026, 10:28 AM:

```
   Bid   Orders        Qty  |  Offer  Orders          Qty
  0.00        0          0  |   4.33     687   28,46,239
  0.00        0          0  |   4.34      13        2,240
  0.00        0          0  |   4.35      12        2,002
  0.00        0          0  |   4.36      12        6,675
  0.00        0          0  |   4.37      12        2,182
 Total                   0  |  Total           46,46,100
```

Zero bid at every level. 46.4 lakh shares queued to sell.

- A **stop-loss cannot trigger** — nothing to execute against.
- A **market order cannot execute** — a market order needs a bid.
- **SL-M, GTT, bracket orders: all equally useless.** They are all instructions to trade, and there is no counterparty.

**Plan every position on the assumption that exit is unavailable for 5+ consecutive sessions.** Five lower circuits at 5% = −22.6%.

---

## 4. Tick size is a tax, and it scales inversely with price

Minimum price increment on BSE/NSE equity is **₹0.01**.

| Price | 1 tick | Band (5%) in ticks |
|---|---|---|
| ₹0.82 | **1.22%** | ~4 ticks |
| ₹1.32 | **0.76%** | ~12 ticks |
| ₹3.34 | **0.30%** | ~33 ticks |
| ₹15.53 | 0.06% | ~155 ticks |

Below ₹10 the price grid itself is coarse enough to eat a meaningful share of the daily band. Crossing the spread once costs 0.3–1.2%; a round trip doubles it. Before brokerage, STT, exchange fees, GST and stamp duty.

**Separately: quoted spreads can be far worse than one tick.** CHANDRIMA on 09 Sep quoted **buy 15.53 / sell 16.15 — a 4.0% spread**, on a stock with a 10% band.

---

## 5. Surveillance frameworks — what actually happens when the exchange notices

Verified against the NSE GSM FAQ (v. 15 Apr 2025) and exchange surveillance documentation.

### GSM — Graded Surveillance Measure (four stages)

| Stage | Price band | Settlement | Trading frequency | Buyer deposit (ASD) |
|---|---|---|---|---|
| I | 5% or lower | Regular | Daily | — (100% margin) |
| II | 5% or lower | **Trade for Trade** | Daily | **50% of trade value** |
| III | 5% or lower | Trade for Trade | **Once a week (Monday)** | **100% of trade value** |
| IV | 5% or lower | Trade for Trade | Once a week (Monday) | 100%, **no upward price movement permitted** |

Two details that are easy to miss and expensive to learn:

- **The ASD is paid by the buyer, in cash, on T+1 — and it is not refundable even if you sell the shares later.** Buying a GSM Stage III stock means posting 100% of the trade value in cash that you do not get back.
- **Stage IV permits no upward price movement.** The stock can only go sideways or down.

### ESM — Enhanced Surveillance Measure (small/micro-caps)

Applies to low-market-cap companies (threshold has moved with framework updates; broadly the sub-₹1,000-crore universe — i.e. **exactly the stocks in our watchlist**).

**Primary Source Circulars:**
- **Original Framework:** BSE Notice 20230602-44 / NSE Circular NSE/SURV/56948 (Effective 5 June 2023).
- **Joint ESM Revision (Current Regime):** **BSE Notice 20230718-46** and **NSE Circular NSE/SURV/57609** (Dated 18 July 2023, Effective 24 July 2023).

| Stage | Price band | Settlement | Trading |
|---|---|---|---|
| I | 5% (or 2% if already applied) | Trade to Trade | Daily (Continuous) |
| II | **2%** | Trade to Trade | **Periodic call auction on ALL trading days** |

#### Periodic Call Auction Structure (SEBI CIR/MRD/DP/6/2013 & CIR/MRD/DP/38/2013)
ESM Stage 2 trades in 6 discrete one-hour auction sessions across the day (first session starts at 09:30):

| Sub-phase | Duration | Rule |
|---|---|---|
| Order Entry / Cancellation | 45 minutes | Orders can be entered, modified, or cancelled |
| Random Closure | 44th–45th minute | System-driven random stop; order modification freezes |
| Order Matching | 8 minutes | Single equilibrium price matched on price-time FIFO |
| Buffer / Transition | 7 minutes | System transition to next hourly session |

**Matching at ±2% Limit:** Matches on price-time priority (FIFO). Sellers are fully filled if buyers exceed sellers; buy orders fill by time priority until contra-volume is exhausted.

#### Pre-Open Window Architecture (SEBI Circular HO/47/11/11(3)2025-MRD-POD2/I/2765/2026, Sept 2026)
- **09:00 to 09:05:** Order entry, modification, and cancellation (Market & Limit orders).
- **09:05 to 09:10:** Order entry and cancellation only for Limit orders (system random close between 09:08 and 09:10).
- **09:10 to 09:12:** Order matching and trade confirmation.
- **09:12 to 09:15:** Transition buffer to continuous trading.

---

### 5b. Broker Execution & Settlement Constraints (Zerodha T2T / BTST Policy)

A strategy that assumes intraday or T+1 morning selling can be completely blocked at the broker layer:

1. **Strict BTST Block on T2T / Surveillance Securities:**
   - Zerodha explicitly specifies: *"BTST trading is not available on Trade to trade stocks, stocks under ASM and GSM."*
   - For Trade-to-Trade (`BE`, `T`, `XT`), shares **must settle into the Beneficiary Owner (BO) demat account** before selling is permitted.
   - Selling uncredited T1 shares is rejected by the broker RMS.
2. **20% Peak Margin on Sell Execution:**
   - Selling T1 holdings requires upfront margin (20% to 40%).
   - Sale proceeds from T1 sales cannot be reused on trade day until Early Pay-In (EPI) is completed.
3. **Auction Close-Out Risk:**
   - Selling uncredited shares in illiquid scrips risks short-delivery if the seller counterparty fails, triggering mandatory exchange buy-in auctions with up to +20% penalty.

> **Bottom Line:** For any stock in `BE`, `XT`, `T`, or under ESM/GSM/ASM, **do not assume an exit can be executed on T+1 morning**. Exit availability begins only after demat credit (typically post-clearing on T+1 evening or T+2).

---

### ASM — Additional Surveillance Measure

- **Long-term ASM:** four stages, 100% margin throughout, bands progressively reduced, Stage 4 = 5% band + Trade for Trade. **Intraday trading blocked at every stage.**
- **Short-term ASM:** single stage, 100% margin, no band change, typically 1–4 weeks.

### Series codes

- **BE / T2T = Trade for Trade (surveillance).** No intraday, gross settlement, compulsory delivery.
- **GATECH-BE on NSE is already in this series.** That is a stock the exchange has already flagged.

> **Any of these appearing on a stock you hold is a sell signal, not a fact to note.** They restrict *your* ability to exit; they do not restrict the operator's, who is generally out before the measure lands.

---

## 6. Reading an order book for operator presence

| Observation | Reading |
|---|---|
| Huge bid qty in **very few orders** (e.g. 40,00,000 in 4 orders) | Manufactured wall. Operator maintaining the lock. |
| Round numbers just under a threshold (9,99,999 / 10,00,010) | Deliberate placement, not organic |
| Huge bid qty across **hundreds of orders** | The crowd has arrived — late |
| Offer side appearing and **growing** while bid queue shrinks | Distribution into the queue. Exit. |
| Bid queue growing faster than volume clears | Lock tightening; fill probability falling toward zero |
| Both sides populated, spread ≤1% | **Actually tradeable.** This is the only state worth entering in. |

---

## 7. The legal line

Observing a price pattern and trading it independently is ordinary trading.

Participating in the scheme is not: acting on tips from the operators, trading on a pump group's calls, operating as one of a coordinated set of accounts, or forwarding the message to bring in more buyers. The **SEBI (Prohibition of Fraudulent and Unfair Trade Practices) Regulations** reach participants, not only organisers. "I only made a little" is not a defence, and consequences run to disgorgement, monetary penalty and market bans.

### SEBI Enforcement Precedents (Primary Source Jurisprudence)
- **Sadhna Broadcast Order (May 2025):** ₹21.45 Cr penalty on 59 entities + ₹58.01 Cr disgorgement at 12% interest. Liability placed exclusively on promoters, YouTube operators, and synchronized volume creators.
- **Sharpline Broadcast Order (March 2023):** Small retail shareholders surged from 517 to 20,009 while insiders dumped. SEBI targeted the pump organizers.
- **Mauria Udyog Order (June 2026):** 221 entities impounded for ₹143.79 Cr disgorgement.
- **Retail Trader Protection Principle:** In all located SEBI pump-and-dump enforcement orders, **no unconnected retail buyers were prosecuted merely for buying on unsolicited SMS/YouTube tips**. SEBI explicitly treats retail buyers as the **victims** of the market manipulation scheme.
- However, to maintain uncompromised institutional integrity:
  **Rule 0 applies unconditionally: Independent algorithmic observation only. Zero tips, zero pump channels, zero coordinated trading.**

---

## Change log

| Date | Who | Change |
|---|---|---|
| 2026-09-09 | Claude | Created. GSM/ESM/ASM tables verified against NSE FAQ 15-Apr-2025 and exchange surveillance docs. |
| 2026-09-10 | Antigravity | Added official regulatory plumbing from exchange archives: BSE inward tick truncation (Item 1.6), verified ESM revision circular IDs (BSE Notice 20230718-46 / NSE Circular NSE/SURV/57609), 6-session PCAS hourly schedule (SEBI 2013 circulars), Zerodha T2T BTST block & settlement constraints, and SEBI PFUTP landmark rulings. Corrected CCDL queue ratio to 0.45%. |
| 2026-09-11 | Antigravity | Implemented Claude's Rule 9 Liquidity Gate (15% max daily volume participation limit; 2-day clearable horizon), Rule 10 Precedence Hierarchy, operator spoof/bid-wall filter (>15x volume), and Chrome CDP staleness monitoring. Corrected citation byline per Claude Loophole 5. |
