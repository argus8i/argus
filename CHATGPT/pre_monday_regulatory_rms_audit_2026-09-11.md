# Pre-Monday regulatory, RMS and logging audit

**As-of:** 11 September 2026 IST  
**Scope:** CDSL, ANGELONE, SUZLON, INOXWIND, IREDA, RVNL, COCHINSHIP and BDL  
**Operating state:** Observation only; no broker orders or capital deployment.

## Executive verdict

1. The official NSE corporate-action calendar returned **none of the eight symbols** for ex-dates between 12 and 26 September 2026. The alleged BDL 21-September ex-dividend is therefore not an active NSE calendar item as of this audit, rather than merely “unverified.” This remains a point-in-time result and must be refreshed each pre-open.[1]
2. None of the eight is added to or deleted from **Nifty Midcap 150** or **Nifty Smallcap 250** in the 30-September reconstitution. There are, however, three other effective-30-September index events: SUZLON enters Nifty Next 100, RVNL leaves Nifty Housing and Nifty PSE, and COCHINSHIP enters Nifty500 Multicap Infrastructure 50:30:20.[2]
3. No active DIPAM OFS/divestment notice for RVNL, IREDA, COCHINSHIP or BDL was identified for 12–26 September. DIPAM lists the current COCHINSHIP 4.58% OFS as completed, with receipts of ₹1,711.24 crore and post-OFS government holding of 63.33%.[3][4] No official promoter lock-in-expiry notice was identified; that sub-check remains **UNKNOWN**, not “none guaranteed.”
4. Zerodha’s equity margin calculator, updated 10 September 2026, lists **20% margin / 5× leverage for all eight names**.[5] These are dated RMS parameters and must be read again on Monday; they must not be hard-coded.
5. A 15:15 internal exit is unsafe. Zerodha states that the continuous session for closing-auction-session (CAS) stocks ends at 15:15 and its MIS auto-square-off begins at **15:12** for CAS stocks. The desk’s internal flattening deadline should be **15:10 IST**, with no reliance on RMS square-off.[6][7]
6. `CHATGPT/observation_log.csv` is still schema v2: five rows, zero eligible sessions and zero eligible fillable paper trades. Migration has **not** been committed. A peer-reviewable v3 specification and Monday template accompany this audit.

## 1. Corporate actions and calendar traps

### 1.1 Ex-dates, bonus issues and splits

The official NSE corporate-actions endpoint was queried for 12-09-2026 through 26-09-2026. CDSL, ANGELONE, SUZLON, INOXWIND, IREDA, RVNL, COCHINSHIP and BDL were absent from the returned action set.[1]

**Control:** rerun the exchange query after 18:00 on the prior trading day and again before the Monday universe is frozen. A later filing overrides this result. “Not present as of 11-Sep” is not a forecast that an issuer cannot announce an action later.

### 1.2 Index reconstitution

The official NSE Indices release makes changes effective from 30 September 2026 (close of 29 September).[2]

| Symbol | Midcap 150 / Smallcap 250 change | Other announced change | Paper-engine treatment |
|---|---|---|---|
| CDSL | None listed | None found in release | Normal calendar flag |
| ANGELONE | None listed | None found in release | Normal calendar flag |
| SUZLON | None listed | **Included in Nifty Next 100** | `INDEX_EVENT_UP`, effective 30-Sep |
| INOXWIND | None listed | None found in release | Normal calendar flag |
| IREDA | None listed | None found in release | Normal calendar flag |
| RVNL | None listed | **Excluded from Nifty Housing and Nifty PSE** | `INDEX_EVENT_DOWN`, effective 30-Sep |
| COCHINSHIP | None listed | **Included in Nifty500 Multicap Infrastructure 50:30:20** | `INDEX_EVENT_UP`, effective 30-Sep |
| BDL | None listed | None found in release | Normal calendar flag |

Exact passive-flow rupees and post-rebalance weights are not supplied in the release, so the model must not invent them. During next week these are event-risk annotations, not directional signals; the mechanically important execution window is closer to the 29-September close.

### 1.3 DIPAM and promoter lock-in

DIPAM’s current receipts page shows the COCHINSHIP OFS as completed, not pending.[3] Its minority-stake-sale page describes the OFS route but does not evidence a new live offer for the four names in the requested period.[4] Searches of current official material produced no active RVNL, IREDA or BDL sale notice for the window.

The audit did not locate a current exchange/DIPAM notice proving a promoter lock-in expiry for any of the four. Because absence from a search is not proof of absence, the field must remain `UNKNOWN` until checked against a dated issuer filing/depository lock-in statement. The pre-open gate is therefore:

- active OFS or promoter-sale announcement: exclude for that session;
- lock-in status unavailable: retain `UNKNOWN`, never coerce to `CLEAR`;
- completed historical OFS: record as history, not a live overhang.

## 2. Zerodha RMS and volatility controls

### 2.1 Point-in-time margin matrix

| Symbol | Zerodha margin | Displayed leverage | Status |
|---|---:|---:|---|
| CDSL | 20% | 5× | measured 10-Sep-2026 |
| ANGELONE | 20% | 5× | measured 10-Sep-2026 |
| SUZLON | 20% | 5× | measured 10-Sep-2026 |
| INOXWIND | 20% | 5× | measured 10-Sep-2026 |
| IREDA | 20% | 5× | measured 10-Sep-2026 |
| RVNL | 20% | 5× | measured 10-Sep-2026 |
| COCHINSHIP | 20% | 5× | measured 10-Sep-2026 |
| BDL | 20% | 5× | measured 10-Sep-2026 |

Zerodha explains that cash-equity margin is at least 20% or VaR+ELM, whichever is higher.[8] Therefore “standard 20%” is descriptive of the current calculator, not an entitlement to 5× leverage. The paper engine must store `margin_pct`, source URL and retrieval timestamp with each simulated validation.

### 2.2 When MIS can be unavailable

Zerodha says MIS/CO may be blocked because of high volatility or sudden price movement, low liquidity/volume, or regulatory restrictions including trade-to-trade, GSM and unsolicited-SMS categories.[9] Other ordinary rejection causes—insufficient available margin, invalid tick/trigger, quantity freeze or a product not allowed for that instrument—must be captured from the exact RMS response rather than guessed.

**Required rejection state machine (paper only):**

1. Set `rms_validation_state=REJECTED_RMS`, `execution_state=NOT_SUBMITTED`, fill quantity zero and P&L blank.
2. Preserve timestamp, raw reason, requested product/order type/quantity/prices, available funds, required margin, surveillance snapshot and margin-source timestamp.
3. Do not count it toward the 20 fillable-trade gate. It may belong to a completed prospective session, but a rejection is not a fill.
4. Do not automatically convert MIS to CNC, weaken the stop, or repeatedly resubmit. One revalidation is allowed only if a predeclared transient-data condition is repaired while the original signal is still valid.
5. Under Rule 1, Monday performs this as a simulation only. No order reaches Kite.

### 2.3 Closing-auction correction

For CAS-eligible shares Zerodha gives a 15:12 MIS auto-square-off time and a 15:15 end to continuous trading.[6][7] Therefore:

- strategy flattening deadline: **15:10:00 IST**;
- signals after the configured last-entry time are `REJECT_TIME_GATE`;
- no simulated fill may use the CAS print to claim a continuous-session exit;
- auto-square-off is an exception outcome, not the intended execution path.

## 3. Observation-log migration and Monday format

### 3.1 Verified current state

`CHATGPT/observation_log.csv` has 46 columns and five rows: one example, three retrospective user-reported live trades and one market observation. Every row has `counts_toward_paper_gate=false`. It has no track, strategy-version, session, feature-cutoff, decision-time or RMS fields. Consequently the unified schema was proposed but **not committed**.

Gate status remains:

- prospective sessions: **0 / 60**;
- realistically fillable paper trades: **0 / 20**.

### 3.2 Monday no-leakage contract

The accompanying `observation_log_schema_v3_proposal.md` and `monday_orb_paper_template.csv` define the migration for peer review. Core rules:

- freeze the eight-symbol universe and strategy/parameter hashes before 09:15;
- historical same-bucket median may use only data available through T−1 (Friday 11-Sep for Monday 14-Sep);
- define the ORB as `[09:15:00, 09:30:00)` and calculate it only after the window closes;
- require `decision_time_ist >= 09:30:00`; use the first observable post-decision quote/depth for simulated submission, never the completed candle’s high/low as an assumed fill;
- log rejects, no-signals, partials and fills—not only winners;
- one session-summary row per date may increment the 60-session counter after the session is complete; symbol rows must not multiply the session count;
- a paper trade increments the 20-fill counter only after prospectively recorded gates and a realistically executable post-decision fill;
- `actual_order_sent=false` is mandatory under Rule 1.

## Decision

Monday may proceed as a **paper observation session only**, subject to pre-open refresh of corporate actions, surveillance and Zerodha margins. The current eight-name basket is not cleared for live capital. No model expectancy has yet been established.

## Sources

1. [NSE corporate actions calendar and query interface](https://www.nseindia.com/companies-listing/corporate-filings-actions) and official API query for 12–26 September 2026: `https://www.nseindia.com/api/corporates-corporateActions?index=equities&from_date=12-09-2026&to_date=26-09-2026`.
2. [NSE Indices — Changes in constituents of equity indices, 10-Aug-2026](https://www.niftyindices.com/Press_Release/ind_prs10082026.pdf).
3. [DIPAM — Disinvestment receipts details](https://dipam.gov.in/disinvestmentReceiptsDtls).
4. [DIPAM — Minority stake sale](https://dipam.gov.in/minority-stake-sale).
5. [Zerodha — Equity margin calculator, updated 10-Sep-2026](https://zerodha.com/margin-calculator/Equity).
6. [Zerodha — Closing Auction Session explainer](https://zerodha.com/z-connect/general/everything-you-need-to-know-about-closing-auction-session-cas).
7. [Zerodha Support — Intraday position auto square-off timing](https://support.zerodha.com/category/trading-and-markets/trading-faqs/general/articles/intraday-auto-square-off-timings).
8. [Zerodha Support — Different types of margin](https://support.zerodha.com/category/trading-and-markets/margins/margin-leverage-and-product-and-order-types/articles/different-types-of-margin).
9. [Zerodha Support — Why intraday orders are blocked for some stocks](https://support.zerodha.com/category/trading-and-markets/charts-and-orders/order/articles/intraday-orders-not-allowed-for-some-stocks).
