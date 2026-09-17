# Master Research Specification & Claude Deep-Research Catalog

**Document ID:** `antigravity/analysis/master_research_specification.md`  
**Date:** 2026-09-10  
**Curator:** Yashu (Lead Trader)  
**Target Research Agent:** Claude (Deep Research / Browser Mode Enabled)  
**Context:** Indian BSE/NSE Micro-Cap Circuit Momentum Capture & Microstructure Modeling Framework  

---

## 1. Overview & Research Objective

To transition our autonomous swing-trading framework from heuristic observations to mathematically grounded execution, we require definitive primary-source answers across **six critical domains**. 

This document serves as the repository master specification for the deep-web research to be executed by Claude using its `/boost /browser` capabilities.

---

## 2. The 6-Domain Research Taxonomy

```
                                    MASTER RESEARCH TAXONOMY
====================================================================================================
Domain 1: Exchange Auction Architecture & PCAS Plumbing (SEBI / BSE / NSE)
Domain 2: Broker Settlement, DDPI & Time-of-Day Execution Constraints (Zerodha Kite)
Domain 3: Regulatory Surveillance Frameworks, Criteria & Prediction (ESM / GSM / ASM)
Domain 4: Microstructure Queue Modeling, Volume Clustering & Drain Calibration
Domain 5: Corporate Forensics, Delivery Divergence & SMS Operator Syndicates
Domain 6: Mathematical Strategy Optimization & Payoff Asymmetry Resolution
====================================================================================================
```

### Domain 1: Exchange Auction Architecture & PCAS Plumbing
* **Rationale:** CHANDRIMA is locked in ESM Stage 2 (2% band + Periodic Call Auction). Without knowing the exact 6-session auction schedule, random stoppage intervals, and clearing allocation rules under revised BSE Notice `20230718-40` / NSE `NSE/SURV/57618`, queueing an exit in Stage 2 is blind guesswork.
* **Core Topics:**
  1. Complete 6-session PCAS daily timetable (e.g., 09:30–10:15, 10:30–11:15, etc.).
  2. The 1-minute random stoppage window during order collection.
  3. Price discovery and clearing allocation algorithms when orders are pegged at ±2% limits (FIFO vs pro-rata).
  4. Pre-open (09:00–09:08) order priority carryover into continuous market (09:15).
  5. BSE/NSE tick rounding rules (inward truncation vs mathematical rounding) for sub-₹10 securities.

### Domain 2: Broker Settlement, DDPI & Time-of-Day Execution Constraints (Zerodha Focus)
* **Rationale:** A 1-to-2 day quick momentum capture strategy relies entirely on T+1 morning execution. If Kite RMS blocks sell orders at 09:00 AM due to uncredited depository payout, lack of DDPI, or 20% upfront margin requirements, pre-emptive exits fail.
* **Core Topics:**
  1. Exact time-of-day when Kite RMS allows selling T+1 Trade-to-Trade (`T`, `XT`, `BE`) holdings (Q10).
  2. DDPI (Demat Debit and Pledge Instruction) vs CDSL EDIS (TPIN) requirements for selling unsettled BTST shares.
  3. SEBI 20% upfront peak margin debit on unsettled sell transactions and required ledger cash.
  4. Exchange self-auction mechanics and financial penalties (+20% close-out) for short deliveries in T2T securities.

### Domain 3: Regulatory Surveillance Frameworks, Criteria & Event Lead Times
* **Rationale:** Band narrowing ($20\% \to 10\% \to 5\% \to 2\%$) destroys liquidity and inverts strategy payoff. Surveillance actions occur overnight without broker notifications. Identifying quantitative criteria and announcement calendars transforms surveillance from a random hazard into a predictive exit filter.
* **Core Topics:**
  1. Published quantitative formulas for ESM Stages 1 & 2, GSM Stages I–IV, and Short/Long-Term ASM.
  2. Weekly/fortnightly publication schedule of exchange circulars (e.g., Friday evenings post-18:00 IST).
  3. Minimum mandatory holding periods within ESM Stage 1 before Stage 2 escalation.
  4. Historical lead time between surveillance announcements and cycle price tops (Q8).
  5. GSM Additional Surveillance Deposit (ASD) debit timing, lockup duration, and refund rules.

### Domain 4: Microstructure Queue Modeling, Volume Clustering & Drain Calibration
* **Rationale:** Claude's red-team audit established that morning queue-drain exit discipline is worth ~27 percentage points over freezing. However, `QUEUE_MULT` (2.0) and `ZERO_BID_RATE` (0.15) remain uncalibrated guesses in `claude/models/fill_model.py`.
* **Core Topics:**
  1. Empirical distribution of sell-queue depth to daily volume ($Q_{\text{queue}} / V_{\text{day}}$) on circuit-locked days (Q11).
  2. Intraday volume clustering profile on lower-circuit sessions (morning cross vs continuous matching).
  3. Empirical probability of receiving a partial vs full fill on Day 1, Day 2, and Day 3 of an adverse descent.
  4. Historical frequency of true `ZERO_BID_RATE` sessions across BSE/NSE micro-caps.

### Domain 5: Corporate Forensics, Delivery Divergence & SMS Operator Syndicates
* **Rationale:** CCDL was promoted by bulk SMS syndicate "DelightAdvsor" at ₹1.32. Trading on unsolicited tips violates Rule 0 and exposes participants to SEBI PFUTP liability. Identifying forensic distribution signatures provides an independent exit trigger before circuits reverse.
* **Core Topics:**
  1. 4-quarter shareholding trends, promoter pledging, and retail shareholder expansion for CROPSTER, CHANDRIMA, CCDL, and GATECH (Q4).
  2. BSE Bulk/Block Deal filings ($\ge 0.5\%$ equity) for all four case studies during their markup and distribution.
  3. Daily Deliverable Quantity to Traded Quantity (`Delivery %`) trends preceding cycle reversals.
  4. SEBI PFUTP enforcement orders against bulk SMS promoters ("DelightAdvsor", *Sadhna*, *Sharpline*), recipient legal liabilities, and regulatory precedent.

### Domain 6: Mathematical Strategy Optimization & Payoff Asymmetry Resolution
* **Rationale:** Claude mathematically proved that targeting +4.5% against a −10.9% queue-drain stop requires an unrealistic 74.6% win rate to break even. We must determine the exact parameter space where a short-horizon strategy achieves positive net expectancy.
* **Core Topics:**
  1. Resolution of payoff asymmetry: Evaluating holding for 2-day capture (+10.25%), filtering for 10%/20% bands, or restricting entries to Stage 0/1 accumulation bases (Rule 6).
  2. Empirical transition probabilities of Upper Circuit follow-through ($P(\text{Lock}_{T+1} \mid \text{Lock}_T)$).
  3. Empirical forward return and survival distributions of securities entering ESM Stage 2 (recovery vs terminal illiquidity).
