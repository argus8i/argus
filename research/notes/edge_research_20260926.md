# Edge research, 26 Sep 2026: what survives costs

Author: Claude (quant lead). Data: sealed design snapshot `p7_design_20260926` (`72a444d7…`), design window
2022-01-03 .. 2024-09-30 only. Every look is in `research/evidence/trials_registry.csv` (T0056–T0119).

## Part A. Design-window verdicts (before any holdout)

Method (`research/studies/strategy_lab.py`):
- one fixed specification per idea, with no parameter search;
- returns from the entry open to the exit close, reported raw, market-adjusted, and **beta-adjusted** (the alpha);
- cost hurdles: CNC 0.28%, MIS 0.12%;
- standard errors clustered where trades share risk (the entry date, week, month or expiry);
- CANDIDATE needs alpha > cost at t ≥ 3 and at least 2 of 3 years positive.

### Antigravity's 8-strategy blueprint, checked

| Claim | Antigravity said | Our data says | Verdict |
|---|---|---|---|
| EXPIRY_RELIEF_RALLY | +2.10% / 5 d, 67% win, 4,011 setups, t > 8 | raw +2.13%, 67% win, **1,212 setups on 32 expiries**, beta-adj alpha **+1.03% net, t 4.3** (by expiry; its t > 8 pooled correlated trades) | **Real: CANDIDATE** |
| BAN_EXIT_RELEVERAGING | +2.00% gross, +1.73% net | +0.15% beta-adj, **−0.13% net**, t −0.4. Its +2.00% is the subset that re-entered the ban, **known only afterwards (look-ahead)**: re-entered +1.95%, did not +0.30% | No edge |
| PEAD 15–20 d | +2.62% gross | +2.85% raw, alpha **+2.03% net, t 2.8** (by month; t 3.5 by date); 2 of 3 years | PROMISING: holdout blocked by the announcement-data gap (below) |
| BULK_ACCUMULATION | +1.85%, 335 setups | **no delivery-% or bulk/block-deal data exists on disk**, so the number could not have come from our data | Not testable |
| TOTM_SIP | +0.92% / 5 d | NIFTY 0.61% vs 0.22% on other 6-day windows, difference t 1.0 | No edge |
| RESIDUAL / XSEC momentum | +2.1%/month | top-5 alpha +1.35%/month, **t 1.05 on 22 months** | Too few months to tell |
| AUCTION_GAP_AND_GO | +0.85%, 56% win, 601 gaps | **+0.07% net, t 0.6, 45% win**, 1,118 trades (1-minute simulation) | False |
| CAPITULATION_REVERSAL | +0.84% | −1.2% alpha, 22 signals | No edge |

### Other ideas tested

| Idea | Result | Verdict |
|---|---|---|
| Earnings-announcement premium (pre-results run-up) | −0.28% alpha before results; −0.66% through results (t −3.7) | No edge (long) |
| Results × large gap (Codex) | intraday +0.38% net, t 1.3 (open fill); 5-day +0.19% | No edge |
| 52-week-high top 5 | +1.25%/month, t 1.7, 22 months | Too few months |
| PEAD dated by board meeting | +1.16% alpha, t 1.8; 2022 negative | Not enough |

### The expiry rebound, stress-tested (T0101–T0104, T0114–T0117)

- **Tied to the expiry.** Alpha net of cost by signal day relative to expiry:

  | Offset (sessions) | −3 | −2 | −1 | **0 (expiry)** | +1 | +2 | +3 |
  |---|---|---|---|---|---|---|---|
  | Alpha net of cost | −0.13 | +0.27 | +0.73 | **+1.03** | +0.32 | +0.16 | −0.19 |

  The same filter on every non-expiry day loses −0.47% (t −3.9).
- **Consistent across time:** 25 of 32 expiries are positive. Dropping the 3 best expiries still leaves t 4.1.
- **Filters and regimes:** the stock-specific (market-adjusted) filter gives t 3.8. It is positive in falling markets (t 3.7) and in rising ones (t 2.2).
- **Exact v1 trading rules:** +0.494R net per trade (₹554), t 3.9, 65% winners, every year positive. The rules are a pre-open buy, a 3% stop, half booked at +1.5R, exit at the E+5 close, Dhan CNC fees and A1 sizing.
- **Capacity:** taking only the 3 most oversold per expiry gives +0.41R, t 2.6.

### The ban-entry short, after Codex's red-team (T0107–T0113)

Codex found five defects in the 15-minute simulation, and all are fixed:

- **Exposure before entry:** the position was exposed to 09:15–09:19 prices, before the VWAP it fills at is known.
- **Wrong exit time:** the "15:05" exit actually filled at the 15:00 bar's open.
- **Gap-through-limit fills:** a stop order that gapped past its limit was still filled at that limit.
- **Unverified data:** the one-minute files were not checked against the sealed manifest.
- **Missing checks:** there was no event-day eligibility check and no ban-list coverage check.

The one-minute simulator (13 regression tests, written first) gives:

| Entry | Net R | t | ₹/trade |
|---|---|---|---|
| 09:15–09:19 VWAP proxy, released at 09:20, SL 3% (**primary**) | +0.118 | 3.86 | 157 |
| same, 2 ticks slippage | +0.112 | 3.66 | 149 |
| pre-open auction open, SL 3% | +0.199 | 5.97 | 265 |
| worst first-minute price, SL 3% | +0.004 | 0.13 | 5 |

The edge sits in the first minutes. The auction price captures most of it, and the worst first-minute fill captures none.

### Data facts found today

- **Results announcements:** the NSE announcements feed has no results filings from 2024-11-14 onward, and almost none in 2022 Q3–Q4. The 20-day results-drift holdout cannot run on NSE data.
- **The BSE results-filings backup (Antigravity staging):**
  - 6,044 timestamped filings, about 200 per quarter, 2021 Q1 to 2026 Q3.
  - Against NSE on 2021–24 it matches with a median gap of 3 minutes; 93% are within 60 minutes and 96% map to the same "public from" session.
  - It is usable for the results-drift holdout **if its source and provenance are approved** (decision 7).
- **The official open:** the Upstox daily open equals NSE's official open (the pre-open auction price) on 100% of same-scale days in 10 sampled stocks.
- **The live universe table:** `reference/universe_daily.parquet` is a stale build (25 Sep 23:39, flag `FO_BAN_HISTORY_MISSING`). The ban-aware tables live inside the sealed snapshots. The shadow runner builds its own universe, so it is not affected.

## Part B. Holdout (one run each, locked pre-registrations)

To be completed after the runs.
