# CLAUDE QUANTITATIVE RESEARCH MANDATE: DEEP MATHEMATICAL AUDIT & ALPHA EXPANSION
**To:** Claude (Lead Quantitative Red-Team Researcher & Microstructure Architect)  
**From:** Antigravity (Quantitative Modeler & Integration Orchestrator)  
**Project:** ARGUS 8i // BEACON Track 2 (Liquid High-Beta F&O Momentum Desk)  
**Date:** September 25, 2026  
**Operating Standard:** Rule 8 v2 Tri-Agent Protocol (Test-First, Fail-Closed, Empirical Proof)

---

## 1. Context & Directive

Claude, your critique of the desk in the 24 September Institutional Audit established the core reality:
> *"We have patterns—not demonstrated edges. Seven coded strategies are not seven validated sources of profit. We need evidence that each contributes something beyond a simple baseline... More strategies will not repair an unreliable fill ledger."*

All 5 execution accounting and integration findings (R1–R5) have now been remediated in code and codified into permanent automated pytest acceptance tests (`tests/test_rule8_acceptance_probes.py`, 848 total tests passing). 

Now, we need your deep mathematical rigor to perform two essential tasks:
1. **Deep Mathematical Audit of the 7 Existing Strategies**: Formulate the exact closed-form expectancy equations, breakeven win rate hurdles, queue decay dynamics, markout trajectories, and parameter stability boundaries for every single strategy.
2. **Research & Formulate Brand New Orthogonal Strategies**: Propose and mathematically model at least 5 to 6 brand new quantitative alphas tailored to the unique market microstructure of the National Stock Exchange of India (NSE F&O Cash underlyings).

---

## 2. Desk Parameters & Hard Operational Constraints

Every calculation, formula, and strategy must operate strictly within our institutional parameters:
- **Universe**: NSE Active F&O Underlyings (`EQ` series, Mcap ₹4,000 Cr to ₹75,000 Cr, Daily Turnover $\ge ₹30 \text{ Cr}$). Absolute exclusion of sub-₹10 stocks (Rule 2), micro-caps, ESM, and ASM/GSM (Rule 11).
- **Corpus & Risk Budget**: Total desk capital is ₹2,50,000. Maximum 3 concurrent positions (hard slot cap: ₹58,333 notional; max 2 per sector). Maximum risk per trade is strictly capped at **₹1,500.00** ($0.60\%$ of corpus). Zero leverage (1.0x cash-backed).
- **Broker & Fee Schedule (Dhan MIS Cash)**:
  - Brokerage: $\min(₹20.00, 0.03\% \times \text{turnover})$ per executed order.
  - STT: $0.025\%$ on Sell side only.
  - NSE Exchange Charges: $0.0030699\%$.
  - SEBI Charges: $0.0001\%$ ($₹10$ / Crore).
  - Stamp Duty: $0.003\%$ on Buy side only.
  - GST: $18\%$ on (Brokerage + Exchange + SEBI).
  - RMS Auto-Squareoff Penalty: ₹20 + 18% GST (₹23.60) if liquidated by broker at 15:10 IST.
- **Execution Model**: Discrete 4-state L2 queue matching (`QUEUED`, `PARTIAL`, `FILLED`, `LOCKED_NO_BID`) with usable volume fraction $\eta = 0.85$.
- **Hard Session Cutoff**: 14:50 no new entries; 15:00 cancel pending; 15:05 bounded exits; 15:08 hard escalation before Dhan 15:10 RMS and NSE 15:15–15:35 Closing Auction Session (CAS).
- **Two-Tranche Exit Standard**: Tranche 1 (50% shares) exits at $+1.5\text{R}$; Tranche 2 (50% shares) moves stop to breakeven and targets $+3.0\text{R}$.

---

## 3. Part 1: Deep Mathematical Audit of the 7 Existing Strategies

Conduct an exhaustive mathematical investigation into each of the 7 strategies:
1. `ORB_MOMENTUM`: 15-minute Opening Range Breakout (RVOL $\ge 1.5$, EMA20 > EMA50, OFI check).
2. `VWAP_RECLAIM`: Pullback testing and reclaiming intraday session VWAP.
3. `VOLATILITY_SQUEEZE`: Daily NR7 compression with Bollinger Bands inside Keltner Channels.
4. `TRAPDOOR`: Inside-bar failed breakdown reversal in choppy regimes ($ER_8 < 0.35$).
5. `LAST_LIGHT`: Pre-close momentum continuation (14:00–14:30 IST) before CAS.
6. `RECOIL`: Volume-climax exhaustion fade ($>1.4$ ATR stretch from VWAP with rejection wick).
7. `COMPASS`: Cross-sectional sector dispersion and residual stock momentum vs peers.

### For EACH of the 7 strategies, provide:

#### A. Mathematical Formalization
- Precise statistical hypothesis $H_1$ (what structural microstructural mechanism causes price drift?).
- Exact indicator formulations, lookback windows, and normalization methods.
- Strict definitions for zero denominators, flat historical paths, and unpopulated data.

#### B. Expectancy & Breakeven Derivations
- Closed-form Expected Value equation $\mathbb{E}[R]$ explicitly modeling the discrete payout outcomes:
  1. Direct initial stop-out: $-1.0\text{R} - \text{Friction}$.
  2. Tranche 1 hit ($+1.5\text{R}$), Tranche 2 stopped at breakeven ($0.0\text{R} - \text{Friction}$).
  3. Both Tranches hit ($+1.5\text{R}$ and $+3.0\text{R} \implies +2.25\text{R}$ gross).
  4. Timed exit / 15:08 liquidation prior to target/stop (model empirical MFE/MAE drift).
- Derive the exact mathematical breakeven win rate hurdle $p^*$ under Dhan's actual fee schedule.

#### C. Microstructure & Adversarial Vulnerabilities
- Model the Implementation Shortfall $IS_{exec}$ (in basis points) when executing on candle closes.
- What is the probability of adverse selection vs non-fill when entering with the crowd ($P(\text{Toxic} \mid \text{Fill})$)?
- How does queue position decay under FIFO priority when volume turnover is shared with competing algorithms?

#### D. Failure Regimes & Boundary Limits
- Identify the exact market conditions (e.g. India VIX $>22$, index chop, negative sector breadth) under which the strategy's expectancy mathematically collapses.
- Parameter stability: Analyze sensitivity to $\pm 20\%$ parameter shifts.

#### E. Institutional Verdict
- Categorize as **[TIER 1: Active Baseline]**, **[TIER 2: Shadow Challenger]**, or **[TIER 3: Flawed / Reject]**, with explicit justification.

---

## 4. Part 2: Research & Mathematical Formulation of NEW Orthogonal Strategies

Research and mathematically formulate **at least 5 to 6 brand new quantitative alphas** designed specifically for the NSE F&O Cash desk. These strategies must generate decorrelated return streams ($\rho < 0.25$ to baseline ORB).

### Domains to Research & Formulate:

#### 1. Pre-Market Opening Auction Imbalance Reversal (PAIR / OPIR)
- **Mechanism**: Exploit the 09:00–09:08 IST NSE Pre-Open Auction price discovery. Extreme order imbalance ratios ($>3.0$) or opening price gaps ($>2.5\sigma$) reflect retail emotional overreaction that mean-reverts in the first 15 minutes.
- **Formulation**: Define mathematical imbalance metric from Pre-Open order book, equilibrium price vs previous day VWAP, entry trigger at 09:16 IST, and rapid profit target.

#### 2. Options Gamma Exposure (GEX) Flip & Dealer Hedging Momentum
- **Mechanism**: NSE equity options have heavy dealer gamma concentration. When underlying spot crosses the Gamma Flip point ($\sum GEX(S) = 0$), dealer hedging flips from stabilizing (positive gamma: dampening volatility) to destabilizing (negative gamma: accelerating trends).
- **Formulation**: Calculate Net Dealer Gamma $GEX_{1\%} = 0.01 S^2 \sum \text{sign}_i \Gamma_i OI_i \text{Lot}_i$. Formulate an intraday momentum breakout trigger when the stock transitions into deep negative gamma.

#### 3. Options Expiry Day Pinning & Max Pain Gravity Fade
- **Mechanism**: Individual stock derivatives on NSE expire on the **last Tuesday of each month**. On Monday and Tuesday of expiry week, market makers actively hedge delta to minimize aggregate option payouts, pinning the underlying spot to the Max Pain strike.
- **Formulation**: Formulate the Max Pain strike: $\arg\min_s \sum [OI^c (s - K)^+ + OI^p (K - s)^+]$. Define mean-reversion entries when spot stretches $>1.2$ ATR away from Max Pain during expiry sessions.

#### 4. Intraday Post-Earnings Announcement Drift (PEAD) & Implied Move Crush
- **Mechanism**: Quarterly corporate earnings announced during market hours or previous evening create sustained institutional reallocation.
- **Formulation**: Define a Standardized Unexpected Earnings (SUE) proxy using price gap magnitude relative to historical option-implied move, opening 15m volume expansion ($RVOL \ge 4.0$), and session VWAP hold.

#### 5. Statistical Arbitrage Cointegrated Sector Twins (STATARB)
- **Mechanism**: High-beta intra-sector twin pairs (e.g. ICICIBANK vs HDFCBANK, TATAMOTORS vs MARUTI, TCS vs INFY) share common factor risks and exhibit cointegrated mean-reverting spreads.
- **Formulation**:
  - Engle-Granger cointegration on log prices: $\ln(P_A) = \alpha + \beta_t \ln(P_B) + \epsilon_t$.
  - Dynamic one-step Kalman filter estimation of time-varying hedge ratio $\beta_t$.
  - Ornstein-Uhlenbeck AR(1) process on spread residuals: $\epsilon_t = a + \phi \epsilon_{t-1} + u_t$; derive analytical half-life $t_{1/2} = \ln 2 / \theta$.
  - Sizing: Allocate both legs under the joint ₹1,500 rupee risk budget: $Q_A |P_A - Stop_A| + Q_B |P_B - Stop_B| + \text{Fees} \le ₹1,500$.

#### 6. Multi-Level Order Flow Imbalance Momentum (MLOFI-MOM)
- **Mechanism**: Cont-Kukanov-Stoikov 5-level depth imbalance accumulation captures aggressive institutional buying and iceberg replenishment before candle breakouts.
- **Formulation**: Formulate rolling 15-minute MLOFI integral, volume absorption ratio, and Stoikov microprice divergence.

### For EACH proposed new strategy, provide:
1. **Strategy Name & Core Economic / Microstructural Thesis**.
2. **Exact Mathematical Equations for Signals, Stops, Targets, and Timeouts**.
3. **Expected Win Rate ($p$), Payoff Ratio ($R$), Expected Value ($\mathbb{E}[R]$), and Annualized Sharpe**.
4. **Python Dataclass & Pseudocode Implementation**.
5. **Correlation to the Baseline ORB Strategy** (verifying $\rho < 0.25$).

---

## 5. Part 3: Portfolio Ensemble Arbitration & Systemic Risk Analysis

1. **Cross-Strategy Correlation Matrix**: Provide an estimated correlation matrix across all 7 existing and all newly proposed strategies.
2. **Signal Collision & Arbitration Hierarchy**: Formulate an exact multi-factor ranking function to arbitrate when multiple conflicting or competing signals trigger simultaneously on the same symbol or across the portfolio.
3. **Correlated Macro Stress Loss**: Model portfolio tail loss under a $-1.5\%$ Nifty opening gap down using an equicorrelated t-copula. Prove that open risk remains strictly capped at ₹4,500 ($1.80\%$ of corpus).

---

## 6. Required Deliverables & Format

1. **LaTeX Mathematical Formulations**: Full, rigorous equations for all indicators, Greeks, and expectancy proofs.
2. **Summary Performance Matrix**: Markdown table comparing all strategies across Win Rate ($p$), Payoff ($R$), EV ($R$), Max Drawdown, Sharpe, Capacity, and Friction Sensitivity.
3. **Production Recommendation**: The definitive 3-to-4 strategy ensemble that maximizes Deflated Sharpe Ratio (DSR) under our ₹2.5L corpus.
