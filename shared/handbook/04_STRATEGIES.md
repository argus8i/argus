# 04. Quantitative Strategies Catalog & Edge Failure Analyses

**Document Version:** 1.0.0  
**Effective Date:** 2026-09-30 15:30 IST  
**System Status:** **BLOCKED** (0 strategies qualified; Expiry Relief v2 blocked by review; PEAD v2 data-blocked)  
**Strategy Ledger:** [research/decision/register.json](file:///C:/Users/yashw/swing-trades-track2/research/decision/register.json)  

---

## 1. Plain-English Summary

In systematic trading, an **edge** is an economic or behavioral inefficiency in the market that yields positive net profit after paying all transaction costs, exchange fees, and taxes.

Over 122,309 stock-days of testing on Indian equities (2022–2026), Project ARGUS discovered a fundamental market reality:
- **Tight intraday scalping is commercially unviable** for our retail cash equity structure. Why? Round-trip trading friction (taxes, exchange turnover charges, stamp duties, and bid-ask spreads) consumes ~0.12% of traded notional. On tight 1%–2% stops, friction eats up 0.06R to 0.15R on every trade. Gross edges that appeared profitable before costs turned sharply negative after costs.
- **The Solution is Horizon Expansion:** Moving from intraday scalps (holding minutes or hours) to **multi-day swing positions (holding 2 to 5 trading sessions)**. When a strategy captures 3% to 6% moves, transaction friction drops to less than 0.04R, allowing genuine economic edges to survive and compound.

---

## 2. The Intraday Quantitative Failure & Friction Mathematics

In September 2026, the research desk subjected 7 production-candidate intraday strategies to rigorous, cost-adjusted backtesting across the full 2022–2024 design window and 2024–2026 holdout:

```
Gross Edge:    +0.00R to +0.05R   (Slight positive price continuation)
Friction Drag: -0.06R to -0.15R   (STT, SEBI fees, GST, Stamp duty, Spread)
--------------------------------------------------------------------------
Net Edge:      -0.06R to -0.15R   (STRUCTURAL CAPITAL DEPLETION)
```

### Autopsy of the 7 Killed Intraday Strategies

| Strategy | Horizon | Gross Return | Net Expected R | Clustered t-Stat | Register Status | Economic Failure Reason |
|---|---|---|---|---|---|---|
| **`ORB_PROD`** | Intraday | −0.003R | **−0.060R** | −7.40 | `KILLED_ON_DESIGN` | Opening breakouts fail to follow through; morning noise whipsaws tight stops. |
| **`RESID_REV`** | Intraday | −0.036R | **−0.124R** | −3.03 | `REJECTED` | Intraday mean reversion on single-stock residuals overwhelmed by trading costs. |
| **`COMPASS`** | Intraday | −0.015R | **−0.066R** | −3.88 | `KILLED_ON_DESIGN` | Moving-average cross intraday signals lack statistical edge after slippage. |
| **`LAST_LIGHT`** | Intraday | −0.020R | **−0.076R** | −4.12 | `KILLED_ON_DESIGN` | Late-afternoon momentum continuation suffers from closing auction volatility. |
| **`TRAPDOOR`** | Intraday | −0.031R | **−0.090R** | −4.85 | `KILLED_ON_DESIGN` | Breakdown fades in cash equities encounter strong institutional absorption. |
| **`VOL_SQUEEZE`**| Intraday | −0.025R | **−0.077R** | −3.95 | `KILLED_ON_DESIGN` | Bollinger band squeeze breakouts suffer catastrophic false-breakout rates. |
| **`RECOIL`** | Intraday | −0.082R | **−0.147R** | −6.15 | `KILLED_ON_DESIGN` | Trend-pullback scalps fail due to inverted stops and cash shorting restrictions. |

All 7 models are permanently decommissioned from live execution.

---

## 3. Active Candidate: Expiry Relief v2

### Plain-English Economic Edge
In the Indian derivatives market, monthly futures contracts expire on the last Thursday of each month (or Tuesday for certain contracts). Institutional traders and hedge funds holding large losing positions are forced to mechanically roll or dump stock before expiry to avoid physical delivery. This creates intense, artificial selling pressure unrelated to company fundamentals. Once expiry passes, this forced selling pressure abruptly ends, allowing oversold stocks to experience sharp 2–5 session mean-reversion relief bounces.

### Strategy Specifications
- **Rules Pre-Registration:** [`research/studies/prereg/expiry_relief_long_v2.yaml`](file:///C:/Users/yashw/swing-trades-track2/research/studies/prereg/expiry_relief_long_v2.yaml)
- **Engine Implementation:** [`research/shadow/expiry_desk.py`](file:///C:/Users/yashw/swing-trades-track2/research/shadow/expiry_desk.py)
- **Holding Period:** Exactly **5 trading sessions** (CNC delivery, fully compliant with Yashu's 1–5 session approval).
- **Setup & Filters:**
  - Universe: Active F&O stock futures constituents (`EQ` series).
  - Condition: Stock must be among the worst 20-session cumulative performers heading into monthly expiry.
  - Risk Clamp: 5.0% maximum catastrophic stop-loss; no profit target (time-based exit at session 5 close).
- **Data Dependencies:** Daily CM & F&O Bhavcopy, next session's F&O ban list, and official ASM/GSM snapshot fetched between 16:00 IST and 09:00 IST.
- **Current Status:** **`DIAGNOSTIC` / `LOCKED_PROSPECTIVE`**
- **Active Blockers:** Blocked from generating canonical paper evidence by Codex review **`CODEX-T2-01-RECHECK`** (`CHANGES_REQUIRED`), which flagged unpinned surveillance evidence and concurrent shadow journal append race conditions.

---

## 4. Active Candidate: Post-Earnings Announcement Drift (PEAD v2)

### Plain-English Economic Edge
When a publicly listed company reports quarterly financial results that massively beat institutional analyst expectations, market prices do not adjust instantaneously. Due to institutional liquidity constraints, bureaucratic committee approvals, and conservative investor skepticism, large mutual funds accumulate their positions gradually over 3 to 20 trading sessions. This generates persistent, predictable upward drift following the announcement date.

### Strategy Specifications
- **Rules Pre-Registration:** `research/studies/prereg/pead_drift_long_v1.yaml` (currently being updated to v2).
- **Holding Period:** Strictly **5 trading sessions** (mandated by Yashu's decision `OWNER-2026-09-29-02`; 20-session holds are disallowed).
- **Setup & Filters:**
  - Standardized Unexpected Earnings (SUE) or extreme Day-0 market reaction on high relative volume ($\ge 2.5\times$).
  - Entry on Day 1 market open following verified evening filing.
- **Data Dependencies:** Official BSE quarterly results filings (Regulation 33), historical price bars, point-in-time F&O membership.
- **Current Status:** **`DATA_BLOCKED`**
- **Active Blockers:**
  1. 118 holdout stock-quarters lack timestamped result filings, pending the approved NSE announcements backfill.
  2. PEAD v2 pre-registration YAML must be finalized and locked before the 2024-10..2026-07 holdout can be read.

---

## 5. Research Candidate: ORB-In-Play (Catalyst-Filtered)

### Plain-English Economic Edge
While generic 15-minute Opening Range Breakouts fail across the broader universe (as proven by `ORB_PROD`), breakouts in stocks that are "In Play"—meaning they have fresh, material public news released overnight or pre-market—exhibit high directional institutional order flow that can sustain intraday momentum.

### Strategy Specifications
- **Universe Scoping:** ₹4,000–₹75,000 Cr market capitalization band (specifically confirmed by Yashu Decision A).
- **Data Dependencies:** High-frequency intraday candles and real-time NSE corporate announcements with dissemination timestamps.
- **Current Status:** **`HYPOTHESIS / DATA_BLOCKED`**
- **Active Blockers:** The 676-day gap in `events/announcements.parquet` (2024-11-14 to 2026-09-24) prevents holdout testing until the nightly backfill is executed.

---

## 6. Comprehensive Strategy Register (`register.json`)

The authoritative status of all evaluated strategies is recorded in [`research/decision/register.json`](file:///C:/Users/yashw/swing-trades-track2/research/decision/register.json):

| Strategy Identifier | Execution Mode | Design / Holdout Result | Authoritative Status | Notes |
|---|---|---|---|---|
| **`ORB_PROD`** | Intraday MIS | Net −0.060R, t −7.40 | **`KILLED_ON_DESIGN`** | Baseline production intraday ORB. Failed costs. |
| **`RESID_REV`** | Intraday MIS | Net −0.124R, t −3.03 | **`REJECTED`** | No-news-filter residual reversal. Holdout failed. |
| **`COMPASS`** | Intraday MIS | Net −0.066R | **`KILLED_ON_DESIGN`** | Intraday trend filter. Negative expectancy. |
| **`LAST_LIGHT`** | Intraday MIS | Net −0.076R | **`KILLED_ON_DESIGN`** | Late afternoon momentum. Negative expectancy. |
| **`TRAPDOOR`** | Intraday MIS | Net −0.090R | **`KILLED_ON_DESIGN`** | Breakdown fade. Negative expectancy. |
| **`VOL_SQUEEZE`** | Intraday MIS | Net −0.077R | **`KILLED_ON_DESIGN`** | Volatility squeeze breakout. Negative expectancy. |
| **`RECOIL`** | Intraday MIS | Net −0.147R | **`KILLED_ON_DESIGN`** | Trend pullback scalps. Severely negative. |
| **`BAN_ENTRY_SHORT`**| Intraday MIS | Holdout −0.018R, t −0.27 | **`REJECTED`** | F&O ban entry short; design edge collapsed on holdout. |
| **`EXPIRY_RELIEF_v1`**| CNC 5-day | Holdout +0.009R, t 0.09 | **`REJECTED`** | Version 1 failed pass threshold on holdout. |
| **`EXPIRY_RELIEF_v2`**| CNC 5-day | Prospective only | **`LOCKED_PROSPECTIVE`**| Blocked by CODEX-T2-01-RECHECK review. |
| **`PEAD_DRIFT`** | CNC 5-day | Design +2.31% net, t 2.02 | **`UNVERIFIED / DRAFT`** | Pending v2 prereg lock and announcements backfill. |
| **`CAS_REVERSAL`** | CNC Multi-day | None | **`UNVERIFIED`** | Closing auction imbalance reversal. Draft idea. |
| **`SWEEP_RECLAIM`** | Intraday / Swing | None | **`UNVERIFIED`** | Liquidity sweep and level reclaim candidate. |
