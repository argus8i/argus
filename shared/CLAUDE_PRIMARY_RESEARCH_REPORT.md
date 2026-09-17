# Indian Micro-Cap Circuit Microstructure, Surveillance, and Execution Mechanics: A Primary-Source Investigation

**Source:** Claude (Quantitative Red-Team & Lead Modeler)  
**Date:** 2026-09-10  
**Curator:** Yashu  
**Document ID:** `shared/CLAUDE_PRIMARY_RESEARCH_REPORT.md`  

---

## TL;DR
- The regulatory plumbing is fully documented and citable: ESM Stage 2 runs a ±2% band under Periodic Call Auction on all trading days (BSE Notice 20230718-46 / NSE Circular NSE/SURV/57609, effective 24 July 2023 — **not** the 20230718-40/57618 IDs in earlier drafts), and the sub-₹10 price-band puzzle is resolved by an explicit BSE Master Circular rule that a lower band cannot go negative and truncates to one tick, with the range floored inward to keep prices strictly within the percentage band.
- The quantitative micro-questions (upper-circuit next-day continuation probability, resting sell-queue depth vs. volume at lower circuits, and ESM Stage-2 survival/recovery over 20/40/60 sessions) have **no** rigorous India-specific published study; these are genuine data gaps, not findings, and must be computed from NSE/BSE bhavcopy + security-wise delivery archives.
- Legally, SEBI's pump-and-dump orders (Sadhna Broadcast, Sharpline, Mauria Udyog/Hanif Shekh) attach liability to promoters, disseminators, "volume creators," and connected offloaders — not to unconnected retail buyers who merely executed on unsolicited SMS/YouTube tips; no located SEBI order proceeded against ordinary retail buyers on that basis alone.

---

## Key Findings

1. **Circular ID correction.** The joint ESM revision that moved Stage 2 to all-day Periodic Call Auction with a ±2% band is **BSE Notice 20230718-46** and **NSE/SURV/57609**, both dated 18 July 2023, effective 24 July 2023. The original framework is BSE 20230602-44 / NSE/SURV/56948 (5 June 2023).
2. **PCAS session architecture is defined by SEBI CIR/MRD/DP/6/2013 and CIR/MRD/DP/38/2013**, reproduced on NSE's Periodic Call Auction page: one-hour sessions, 45 min order entry/modification/cancellation, random close in the 44th–45th minute, 8 min matching, 7 min buffer.
3. **The sub-₹10 rounding rule is explicit and citable** (BSE Consolidated Master Circular Equity Segment, Item 1.6): a lower price-band value "cannot be specified in negative," so the tick floor applies, and for sub-₹1 scrips "the upper limit shall be adjusted one tick size." This confirms inward truncation to tick, not nearest-tick rounding.
4. **Zerodha T2T/BTST selling is blocked for T2T, GSM, and ASM scrips** — the very categories micro-cap circuit runners fall into — so the momentum-capture premise is structurally constrained at the broker layer.
5. **Empirical surveillance after-effects are well-studied for GSM and ASM but not ESM.** The Aggarwal–Bhatia–Zaveri GSM study and Chari–Inamdar ASM studies document post-inclusion CAR decline and liquidity contraction with recovery only after exit; ESM Stage-2 trajectory is unstudied.

---

## Topic 1 — Auction Architecture, PCAS Plumbing, and Order Priority

### 1a. PCAS mechanics under ESM Stage 2
ESM Stage 2 securities trade **on all trading days** through Periodic Call Auction with a **±2% price band** and **100% margin**, on Trade-for-Trade settlement (BSE Notice 20230718-46 / NSE/SURV/57609, effective 24 July 2023). Before this revision (original June 2023 framework), Stage 2 scrips traded only once a week.

The auction session structure derives from SEBI's illiquid-scrip Periodic Call Auction framework (SEBI CIR/MRD/DP/6/2013 dated 14 Feb 2013, rationalised by CIR/MRD/DP/38/2013 dated 19 Dec 2013):

| Sub-phase | Duration | Rule |
|---|---|---|
| Order entry / modification / cancellation | 45 minutes | Orders may be entered, modified, cancelled |
| Random closure | within last 1 minute (44th–45th min) | System-driven random stop |
| Order matching + trade confirmation | 8 minutes | Single equilibrium price |
| Buffer / transition | 7 minutes | Close current session, open next |

Sessions are one hour each, run through trading hours (first starting 09:30 per the illiquid-scrip framework). Order cancellations/modifications are frozen at the random-close point in the 44th–45th minute.

**Allocation when locked at the ±2% limit:** The equilibrium-price algorithm is standard call-auction logic (maximum executable volume; then minimum order imbalance; then closest to previous close). Within the matched price, orders execute on **price-time priority (FIFO)** — not pro-rata. When buy volume at +2% exceeds sell volume, sellers are fully filled and buy orders fill by time priority until sell quantity is exhausted; the remainder go unmatched and roll to the next session.

**Anti-manipulation penalty (self-trade):** Higher of (a) 0.50% of buy value + 0.50% of sell value (1% total) or (b) ₹2,500 + ₹2,500 (₹5,000 total), deposited to IPF.

### 1b. Pre-open carryover priority
Unmatched limit orders carry into the continuous session on price-time priority retaining their original pre-open timestamp.
**September 2026 regime change:** Under SEBI circular HO/47/11/11(3)2025-MRD-POD2/I/2765/2026, pre-open window restructured: market + limit orders 09:00–09:05, limit orders only 09:05–09:10 with random close 09:08–09:10, matching 09:10–09:12, buffer 09:12–09:15. Key cutoff is 09:05.

### 1c. Sub-₹10 price band and tick rounding
BSE Consolidated Master Circular Equity Segment, Item 1.6: Inward truncation to tick. Confirms CCDL ₹1.38 upper circuit on a ₹1.32 close (1.32 × 1.05 = 1.386 truncated to 1.38).

---

## Topic 2 — Broker Settlement, DDPI, and Time-of-Day Execution (Zerodha)

### 2a. T+1 selling unlock and DDPI vs. TPIN
Zerodha explicitly states: **"You can only sell your T2T stocks on the next trading day (T+1 day)"** and **"BTST trading is not available on Trade to trade stocks, stocks under ASM and GSM."**
For T-series/XT/BE Trade-to-Trade securities, Zerodha does **not** permit BTST-style T+1 selling before the shares are credited to the demat account. The T1 holding cannot be sold until it settles into the Beneficiary Owner (BO) account.

### 2b. SEBI 20% peak margin on sell transactions
Selling T1 (BTST) holdings requires margin (20%–40%), and sale proceeds from T1 holdings cannot be used the same day (usable only after Early Pay-In completes on settlement). Insufficient ledger cash causes RMS rejection.

### 2c. Short-delivery and self-auction on T2T BTST
If counterparty defaults, CC conducts buy-in auction. Close-out penalty is the higher of:
1. Highest price from trade day to auction day, or
2. 20% above auction-day close/settlement price.

---

## Topic 3 — SEBI Surveillance Framework, Triggers, and Lead Times

### 3a. Quantitative inclusion criteria
- **ESM Stage 1:** Mcap < ₹1,000 Cr. High-Low variation thresholds: 3m > 75%, 6m > 100%, 12m > 150% AND positive 3m close-to-close. Action: 100% margin from T+2; T2T; 5% band (or 2% if already there).
- **ESM Stage 2:** Must be already in Stage 1. 5 consecutive days close-to-close ≥ +15% OR monthly ≥ +30% AND PE ≤ 0 or > 2× Nifty 500 PE. Action: T2T, 2% band, 100% margin, PCAS on all trading days.
- **Tenure:** Minimum 90 calendar days in ESM, minimum 1 month in Stage 2.
- **GSM Stage II–IV:** ASD is collected only in cash on T+1, retained until further notice, NOT refunded even if shares sold before quarterly review.

### 3b. Review timetable
ESM reviewed weekly on Fridays post-market. Cannot jump directly from continuous trading into ESM Stage 2 without first spending time in Stage 1.

---

## Topic 4 — Corporate Filings, Shareholding, and Enforcement

### 4a. Identifier Conflict Resolved
- **BSE 539091** corresponds to **Consecutive Commodities Limited (CCDL)** (formerly Consecutive Investments & Trading Co Ltd).
- **Contil India** trades under BSE code 531067.

### 4b. SEBI Enforcement Precedents & Retail Liability (Rule 0)
- **Sadhna Broadcast (May 2025):** ₹21.45 Cr penalty on 59 entities + ₹58.01 Cr disgorgement at 12% interest. Promoters, YouTube operators, and volume creators penalized.
- **Sharpline Broadcast (March 2023):** Small shareholders surged 517 to 20,009. Liability placed on promoters, disseminators, and connected offloaders.
- **Mauria Udyog (June 2026):** 221 entities barred, ₹10 Cr penalty on Hanif Shekh, ₹143.79 Cr disgorgement.
- **Legal Reality for Retail Traders:** Liability under SEBI PFUTP attaches to promoters, telemarketers, volume creators, and synchronized sellers. **In no located SEBI order has liability been imposed on unconnected retail buyers** who merely bought shares after receiving an unsolicited SMS/YouTube tip. SEBI explicitly identifies retail buyers as the *victims* of the scheme.

---

## Topic 5 — Queue Modelling and Execution Drain Calibration

### 5a. Queue Multiplier (`QUEUE_MULT`) Calibration
Yashu's empirical CROPSTER Day 3 exit (12,560 shares filled in 1 hour during a 1.57 Cr session) proves that the queue drains rapidly in morning sessions.
- Realized hourly share: 0.08% of daily volume.
- Supports updating `QUEUE_MULT` from theoretical 2.0 to **1.0** as a calibrated base case prior.

### 5b. Intraday Volume Clustering
Empirical literature (Krishnan & Mishra 2013; Sampath & Gopalaswamy 2020) establishes a **U-shaped volume distribution** on Indian exchanges (high at 09:00–09:30 AM and 15:00–15:30 PM). Morning queue positioning maximizes fill probability during the opening cross.

---

## Topic 6 — Strategy Optimization & Empirical Gaps

1. **Wide-Band Liquidity:** 10% and 20% bands exist only on ordinary, non-surveillance equities. The moment a stock enters ESM/GSM/ASM, bands narrow to 5% or 2%, compressing the risk-reward ratio. Therefore, the strategy must target **Stage 0/1 accumulation bases BEFORE surveillance entry**.
2. **Computable Gaps (To be measured from our Bhavcopy pipeline):**
   - Upper-circuit T+1 continuation probability $P(\text{Lock}_{T+1} \mid \text{Lock}_T)$.
   - Zero-bid session frequency across micro-caps.
   - ESM Stage 2 20/40/60-session recovery vs terminal illiquidity rates.
