# Red-Team Audit Request: Track 2 Quantitative Momentum Platform (₹2–3 Lakh Corpus)

**To:** Claude (Lead Microstructure Analyst, Risk Auditor & Red-Teamer)  
**From:** Antigravity (Quantitative Modeling & Execution Automation)  
**Workspace:** `c:\Users\yashw\swing trades`  
**Governing Protocols:** [`AGENTS.md`](file:///c:/Users/yashw/swing%20trades/AGENTS.md), [`shared/00_PROTOCOL.md`](file:///c:/Users/yashw/swing%20trades/shared/00_PROTOCOL.md), [`shared/track2_liquid/01_MARKET_MECHANICS.md`](file:///c:/Users/yashw/swing%20trades/shared/track2_liquid/01_MARKET_MECHANICS.md)  
**Target:** Track 2 Quantitative Momentum Platform, Terminal UI (`http://127.0.0.1:8766/`), and Sizing Architecture for ₹2,00,000 – ₹3,00,000 INR.

---

## 1. Context & User Mandate

The user has explicitly eliminated the offshore/Australian conduit and multi-asset diversification sleeves (gold, silver, US tech). 100% of the active capital (**₹2,00,000 to ₹3,00,000 INR**, baseline calibrated at **₹2,50,000**) is dedicated exclusively to **Track 2 Momentum (Liquid High-Beta F&O Underlyings in Cash EQ)**.

The user's direct instruction:
> *"Forget about that Australia and all of that bullshit and get to the point. Leave about all of that and also forget about the asset allocation and all of it. Not required. We'd be doing that track to momentum thing in this. Maybe 2 or 3 lakhs would be there in the track to momentum. And then analyze and build whatever platform you want to build. And for this, if we are building something, the cloud [Claude] should be attacking it so that we find any, so that we don't miss out on any problems and then we work back on it."*

---

## 2. Platform Architecture Under Review

### A. Dynamic Universe Discovery & Multi-Factor Ranking
* **Universe:** 214 active NSE F&O underlyings in Cash `EQ` series.
* **Pre-market Quantitative Screening:**
  * Market Cap: ₹4,000 Cr $\le \text{Mcap} \le$ ₹75,000 Cr.
  * Liquidity: Daily Turnover $\ge$ ₹30 Cr.
  * Volatility: $\text{ATR}_{14}\% \ge 3.5\%$.
  * Beta: $\beta_{252} \ge 1.30$.
  * Surveillance: Zero ASM / GSM / ESM / T2T. Dynamic flexing band (`0.0%`).
* **Multi-Factor Ranking Formula:**
  $$\text{Composite Score} = 0.35 \times \text{VolExpansion} + 0.25 \times \text{GapMomentum} + 0.25 \times \text{RelativeStrength} + 0.15 \times \beta$$
* **Sector Diversification:** Maximum 2 scrips per sector cluster in the Top 8.

### B. Multi-Timeframe Alpha & Market Regime Gating
* **Daily Trend Filter:** $\text{Price} > \text{EMA}_{20} > \text{EMA}_{50}$.
* **Macro Regime Filter:**
  * `BULLISH_EXPANSION`: Nifty $> 15\text{m OR High}$ and A/D $\ge 1.20 \implies 2.5\times$ volume confirmation.
  * `NEUTRAL_SELECTIVE`: Nifty inside $15\text{m OR}$ and A/D $\ge 1.0 \implies 3.5\times$ volume confirmation.
  * `DISTRIBUTION_GATED`: Nifty $< 15\text{m OR Low}$ or A/D $< 1.0 \implies$ all long entries blocked.
* **15-Min ORB Breakout Execution (09:30–14:30 IST):**
  * Entry: 15m candle close $> \text{OR High}$.
  * Extension Ceiling: Rejects entry if $\text{Price} > \text{OR High} + 0.5 \times \text{ATR}_{14}$.
  * Degenerate Stop Guard: Rejects entry if stop distance $< 0.5\%$.

### C. Portfolio Risk Governor (Calibrated for ₹2,50,000 Corpus)
* **Single-Trade Rupee Risk ($1R$):** Exactly ₹1,500 per trade (0.60% of corpus).
* **Maximum Concurrent Positions:** Exactly 3 active trades.
* **Aggregate Open Risk Cap:** Capped at ₹4,500 ($3R$, or 1.80% of corpus).
* **Maximum Capital Notional Deployed:** ₹2,00,000 max.
* **Unencumbered Cash Buffer:** ₹50,000 cash buffer strictly protected (no margin borrowing, no pledging, zero CNC delivery rejections).
* **Sector Concentration Limit:** Max 2 positions per sector cluster.

### D. Two-Tranche Split-Exit Execution Engine
* **Tranche 1 (50% shares):** Target $+1.5R$. On fill $\implies$ Tranche 2 stop moved to **Breakeven (Entry Price)**.
* **Tranche 2 (50% shares):** Dynamic ATR trailing stop ($1.5 \times \text{ATR}_{14}$).
* **Reciprocal OCO Cancellation:** If initial stop hit $\implies$ both tranches exit immediately; targets canceled.
* **Broker Cutoffs:** Auto-squareoff at 15:12 IST (CAS eligible) and 15:25 IST (regular non-CAS).
* **Discrete E3 Simulation:** Queue rank + order quantity matching.

### E. Institutional Web Terminal (`http://127.0.0.1:8766/`)
* **Frontend:** Responsive dark-mode terminal (`antigravity/ui/terminal/index.html`) featuring Macro Bar, Risk Governor Dials, Dynamic 8-Stock Radar Matrix, Active Bracket Ledger, Sector Concentration Heatmap, and Real-Time Audit Log.
* **Backend:** `antigravity/daemons/track2_terminal_server.py` serving static assets and real-time REST JSON endpoints (`/api/state`, `/api/audit`, `/api/action/re-scan`, `/api/action/squareoff`).

---

## 3. Specific Attack Vectors for Claude's Red-Team Audit

Claude, deliver your unvarnished, highly technical red-team review attacking the following critical vectors:

1. **₹2–3 Lakh Capital Sizing Stress Test:**
   - How does the portfolio withstand 3 consecutive stop hits ($-₹4,500$, or $-1.8\%$)?
   - What happens under a catastrophic market gap-down (e.g. overnight geopolitical shock where a stock gaps down 5% through the initial stop)? How does our discrete execution model handle gap slippage?
2. **T+1 Cash EQ Settlement Bottleneck:**
   - In Indian markets (SEBI T+1 rolling settlement), if Position 1 is closed at 11:00 AM, can the sale proceeds be used to enter Position 3 on the same day in Cash EQ delivery without incurring margin shortfalls or peak margin penalties?
3. **Broker Cutoff & Squareoff Dynamics:**
   - How robust is our distinction between Zerodha's 15:12 CAS and 15:25 non-CAS squareoffs? In an illiquid market, can market orders at 15:25 cause adverse fills?
4. **Terminal UX & Operator Risk:**
   - Are the fail-closed states clearly visible to the operator? Does the UI prevent accidental duplicate entries or fat-finger overrides?
5. **Claude's Proposed Counter-Rules & Hardening Conditions:**
   - Under what exact microstructural conditions does Claude sign off on this ₹2–3 Lakh Track 2 platform?
