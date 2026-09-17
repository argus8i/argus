# Master Trade Log Index — Decoupled Dual-Track Architecture

**Last Updated:** 2026-09-12 | **Governance:** AGENTS.md Rule 11 (Absolute Track Isolation)

Per AGENTS.md Rule 1 and Rule 11, paper-trading observations and historical executions are permanently partitioned by track. Mixing micro-cap circuit descent trades with liquid 15-minute ORB breakouts destroys statistical expectancy modeling.

---

## [Track 1 Trade Log: Micro-Cap Circuit & Surveillance](file:///c:/Users/yashw/swing%20trades/shared/track1_esm/03_TRADE_LOG.md)
*Dedicated Location:* [`shared/track1_esm/03_TRADE_LOG.md`](file:///c:/Users/yashw/swing%20trades/shared/track1_esm/03_TRADE_LOG.md)
- **Scope:** BSE/NSE Micro-Caps under surveillance (ESM Stage 1/2, PCAS, 2%/5% bands).
- **Strategy:** Rule 7 Pre-Circuit Accumulation Breakout; Rule 5 10-day LC sizing; Day 3/4 pre-emptive UC exits.
- **Historical Baseline:**
  - `CCDL`: +₹1,800.00 (+4.55%) on Day 2 pre-emptive exit.
  - `CROPSTER`: −₹8,500.00 (−14.18%) on Day 3 volume absorption exit.
  - `CHANDRIMA`: +₹45.00 breakeven intraday exit (HIST-03A) / +₹2,750.00 (+4.99%) reported swing exit (HIST-03B).
- **Gate Status:** 0 / 60 Prospective Sessions | 0 / 20 Realistically Fillable Entries.

---

## [Track 2 Trade Log: Liquid High-Beta Momentum](file:///c:/Users/yashw/swing%20trades/shared/track2_liquid/03_TRADE_LOG.md)
*Dedicated Location:* [`shared/track2_liquid/03_TRADE_LOG.md`](file:///c:/Users/yashw/swing%20trades/shared/track2_liquid/03_TRADE_LOG.md)
- **Scope:** Liquid Midcap 150 / Smallcap 250 (`EQ` series) active F&O underlyings.
- **Strategy:** 15-Minute Opening Range Breakout (ORB 09:15–09:30); fixed ₹1,500 rupee risk budget; 1:2.0 / 1:1.55 R:R; intraday MIS or 2–4 day CNC swing.
- **Gate Status:** 0 / 60 Prospective Sessions | 0 / 20 Realistically Fillable Entries.
- **Paper Template:** Ready for Monday 14-Sep live session.

---

## Master Gate Status Summary
| Track | Strategy | Current Capital | Paper Sessions | Paper Fills | Status |
| :--- | :--- | :---: | :---: | :---: | :---: |
| **Track 1 (ESM)** | Pre-Circuit Accumulation | 100% Cash | **3 / 60** | **2 / 20** | Observation Mode (1 Closed Win: +1.48%) |
| **Track 2 (Liquid)** | 15-min ORB Momentum | 100% Cash | **3 / 60** | **3 / 20** | Observation Mode (Active ORB Holds) |

---

## Change log

| Date | Who | Change |
|---|---|---|
| 2026-09-09 | Claude | Created from the 20 screenshots in `data screenshots/`. |
| 2026-09-10 | Antigravity | Updated CCDL to CLOSED (+₹1,800.00 / +4.55% at ₹1.38). |
| 2026-09-10 | ChatGPT | Reconciled three user-reported closed trades; separated reported/gross from net and paper eligibility. |
| 2026-09-11 | Antigravity | Marked CHANDRIMA as DISPUTED per ChatGPT red-team audit. |
| 2026-09-12 | Antigravity | Decoupled architecture under AGENTS.md Rule 11: created dedicated `shared/track1_esm/03_TRADE_LOG.md` and `shared/track2_liquid/03_TRADE_LOG.md`. |
