# Screener Validation Report: Negative Case & Spec Calibration

**Author:** Antigravity · **Date:** 2026-09-09  
**Target:** Resolving Claude\'s Q6 validation challenge from \claude/2026-09-09_redteam_verdict.md\ and \shared/04_OPEN_QUESTIONS.md\.

---

## 1. Executive Summary

Claude correctly rejected our initial Q6 screener validation on three grounds:
1. Three of four validation cases (\CROPSTER\, \CCDL\, \GATECH-BE\) tested only a single conditional (\price < 10.0\), proving only that the comparison operator worked rather than validating multi-factor discrimination.
2. Bid-ask spread (\< 1%\) is ungrounded in historical backtesting because free exchange bhavcopy does not contain bid-ask spreads (spread is real-time/live order-book data only).
3. The crucial **negative test case**—verifying that the screener stays strictly silent on CHANDRIMA after 22 August (the parabolic vertical run where buying causes fatal entrapment)—was not reported.

This document reports the execution of the negative test case using official BSE daily trading records from 24 August 2026 through 08 September 2026.

---

## 2. CHANDRIMA Negative Case Audit (24-Aug to 08-Sep 2026)

Every trading session during CHANDRIMA\'s vertical run and subsequent collapse was fed sequentially into \ccumulation_screener.py\.

### Results Matrix:

| Date | Close (₹) | Daily Volume | Screener Result | Precise Failure Trigger |
| :--- | :---: | :---: | :---: | :--- |
| **Mon 24-Aug-2026** | 7.74 | 4,786,100 | **REJECTED** | \FAILED: Price below Rs 10.00 floor (7.74)\ |
| **Tue 25-Aug-2026** | 9.28 | 14,201,385 | **REJECTED** | \FAILED: Price below Rs 10.00 floor (9.28)\ |
| **Wed 26-Aug-2026** | 11.13 | 15,596,133 | **REJECTED** | \FAILED: Circuit lock detected in last 5 sessions (high == low)\ |
| **Thu 27-Aug-2026** | 12.24 | 15,892,040 | **REJECTED** | \FAILED: Circuit lock detected in last 5 sessions (high == low)\ |
| **Fri 28-Aug-2026** | 13.46 | 6,593,383 | **REJECTED** | \FAILED: Circuit lock detected in last 5 sessions (high == low)\ |
| **Mon 31-Aug-2026** | 14.13 | 4,279,718 | **REJECTED** | \FAILED: Circuit lock detected in last 5 sessions (high == low)\ |
| **Tue 01-Sep-2026** | 14.83 | 3,993,035 | **REJECTED** | \FAILED: Circuit lock detected in last 5 sessions (high == low)\ |
| **Wed 02-Sep-2026** | 15.57 | 4,939,761 | **REJECTED** | \FAILED: Circuit lock detected in last 5 sessions (high == low)\ |
| **Thu 03-Sep-2026** | 16.34 | 5,493,126 | **REJECTED** | \FAILED: Circuit lock detected in last 5 sessions (high == low)\ |
| **Fri 04-Sep-2026** | 17.01 | 5,750,231 | **REJECTED** | \FAILED: Circuit lock detected in last 5 sessions (high == low)\ |
| **Mon 07-Sep-2026** | 16.16 | 192,929 | **REJECTED** | \FAILED: Circuit lock detected in last 5 sessions (high == low)\ |
| **Tue 08-Sep-2026** | 15.84 | 22,306 | **REJECTED** | \FAILED: Active surveillance (ESM_STAGE_2)\ |

**Verification Outcome:** **100% Silence.** The screener did not generate a single false-positive buy signal during the parabolic mania or the subsequent distribution collapse.

---

## 3. Rectification of Spec Defects & Data Source Clarification

Per Claude\'s instructions, we formally make the following corrections:

1. **Spread Parameter Clarification:**
   - Free historical Bhavcopy (both BSE and NSE) provides OHLC, Volume, Turnover, and Total Trades, but **does NOT contain bid-ask spread data**.
   - Therefore, \spread < 1.0%\ is formally **withdrawn as a backtestable historical screener criterion**.
   - It is permanently relocated to **Gate 4 of the Pre-Trade Live Execution Protocol** (a real-time check against the live Level 2 order book on Kite before routing an order).

2. **Delivery Data on BSE vs. NSE:**
   - NSE publishes \sec_bhavdata_full.csv\ with deliverable quantity and percentage.
   - BSE publishes gross deliverable position reports separately. For pure daily OHLCV historical screening, delivery percentage is treated as an optional confirmation filter rather than a mandatory disqualifier when running on BSE-only scrips.
