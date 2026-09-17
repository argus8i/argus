# Institutional Quantitative Glossary & Common Lexicon

**Scope:** Unified definitions shared across **Antigravity** (Center Pane), **Claude Code** (Left Pane), and **OpenAI Codex / ChatGPT** (Right Pane).  
**Repository:** `c:\Users\yashw\swing trades`  
**Last Updated:** 12 September 2026  

---

## 1. Orders & Broker Execution Mechanics

| Term | Institutional Definition |
| :--- | :--- |
| **SL-M (Stop-Loss Market)** | Stop-Loss Market order. Triggers at trigger price, then converts to a market order executing against prevailing liquidity. Guarantees fill in continuous two-sided markets, but exposes trader to unbounded adverse slippage. Prohibited on micro-caps (Track 1); optional under strict conditions on liquid F&O (Track 2). |
| **SL-Limit (Stop-Loss Limit)** | Stop-Loss Limit order. Triggers at trigger price, then submits a limit order at the specified limit price. Caps slippage to limit offset, but introduces non-execution risk (order can trigger and never fill if market gaps through). Standard order type for Track 2 momentum exits. |
| **Limit Offset / Buffer** | The fixed or relative discount below the trigger price where the limit order rests (e.g. 0.5% below trigger for Track 2). Guarantees fill buffer while preventing runaway slippage. |
| **MIS (Margin Intraday Square-off)** | Zerodha intraday equity trading product. Provides leverage for day trading; positions are forcefully auto-squared off before market close (~15:20–15:25 IST). |
| **CNC (Cash and Carry)** | Delivery equity product. Zero leverage; requires 100% upfront cash margin; held overnight across T+1/T+2 settlement cycles. Mandatory for Track 1 micro-caps. |
| **AMO (After-Market Order)** | Orders queued with the broker outside market hours (e.g., 15:45 to 08:59 IST) for automatic transmission at market open (09:00:00 or 09:15:00 IST). |
| **FIFO / Price-Time Priority** | Exchange order-matching rule where orders at the same price are matched strictly according to the chronological timestamp of arrival at the exchange matching engine. |
| **Queue Rank ($R$)** | The cumulative quantity of shares resting in the exchange order book ahead of a specific limit order at the same price level. Determines FIFO fill sequence. |
| **Auto-Square-Off** | Broker Risk Management System (RMS) forced liquidation of open MIS positions near market close (~15:20–15:25 IST). Carries an administrative penalty (₹50 + 18% GST per executed order). |
| **Kite / Zerodha** | The broker platform and API environment utilized by the trading desk for market data, order entry, and account settlement. |

---

## 2. Screening & Quantitative Metrics

| Term | Institutional Definition |
| :--- | :--- |
| **Mcap (Market Capitalization)** | Full company valuation in Indian Rupees (₹ Crore; 1 Cr = 10 million rupees = ₹1,00,00,000). Track 1 targets Mcap < ₹500 Cr; Track 2 requires Mcap ₹4,000–₹75,000 Cr. |
| **DTV (Daily Traded Value)** | Average daily rupee turnover ($\text{Volume} \times \text{Price}$). `dtv_med20_cr` denotes the 20-day median daily traded value. Track 2 requires DTV $\ge$ ₹30 Cr. |
| **Beta ($\beta$)** | Measure of systematic volatility relative to the benchmark index (Nifty 50). Beta > 1.3 indicates high-beta momentum sensitivity required for Track 2 breakouts. |
| **ATR / ATR14** | Average True Range computed over a 14-period lookback. Measures natural market volatility; used to set extension caps and evaluate volatility compression. |
| **Institutional Holding** | Combined equity ownership percentage held by Mutual Funds, Domestic Institutional Investors (DIIs), Foreign Portfolio Investors (FPIs/FIIs), and Insurance companies. Track 2 requires $\ge 15\%$. |
| **Delivery %** | The fraction of total daily traded volume that resulted in actual transfer of share ownership (delivery) rather than intraday speculative churn. |
| **Float** | Tradable shares available to the public. Sovereign Public Sector Undertakings (PSUs) carry restricted float due to 70–75% Government of India (GOI) ownership. |

---

## 3. Opening Range Breakout (ORB) Strategy (Track 2)

| Term | Institutional Definition |
| :--- | :--- |
| **ORB** | Opening Range Breakout strategy. High-probability quantitative system capturing morning momentum following price consolidation. |
| **Opening Range (OR)** | High and low price levels established during the first 15 minutes of trading (09:15:00 to 09:30:00 IST). |
| **OR High / OR Low** | `OR_High` acts as the long entry trigger; `OR_Low` defines the structural stop-loss level. |
| **Volume Confirmation** | Requirement that the breakout 15-minute candle volume exceeds $\ge 2.5\times$ the historical 20-day median volume for the 09:15–09:30 time slot. |
| **Extension Ceiling** | Anti-chase safety guard: Entries are rejected if price has drifted more than `OR_High + 0.5 * ATR14` before order execution. |
| **Degenerate Stop** | Error condition where Entry Price $\le$ Stop Price (OR Low), resulting in zero or negative risk. Screener must immediately reject. |

---

## 4. Risk Management & Position Sizing

| Term | Institutional Definition |
| :--- | :--- |
| **Risk Budget** | Fixed rupee allocation willing to lose per trade (strictly **₹1,500** for Track 2 ORB trades). |
| **Risk per Share** | $\text{Entry Price} - \text{Effective Exit Price}$. The exact capital exposure per single share. |
| **Effective Exit** | The actual price level where the position is liquidated during stop execution (equals Stop Trigger Price for SL-M; equals Limit Price for SL-Limit). |
| **R:R (Risk-to-Reward Ratio)** | The mathematical payoff ratio: $\text{Potential Reward} / \text{Effective Risk}$. Track 2 targets a structural $2 \times (\text{Entry} - \text{Stop})$ profit target. |
| **Analytical Dynamic R:R Curve** | The exact formula governing SL-Limit payoff as a function of stop width $w$: $R(w) = \frac{2w}{w + 0.005(1-w)}$. At $w = 1.67\%$, realized R:R is 1:1.552. |
| **Breakeven Win Rate** | Theoretical win percentage required for zero net expectancy: $\text{WR}_{\text{be}} = \frac{1}{1 + \text{R:R}}$. At 1:1.552 R:R, breakeven is 39.2%. |
| **Max Notional Ceiling** | Hard ceiling on total capital deployed in a single position (strictly **₹1,00,000** for Track 2). |
| **Participation Cap** | Upper limit on order size relative to daily market volume: $\le 0.1\%$ of daily turnover for Track 2; $\le 15\%$ of daily turnover over a 2-session horizon for Track 1 (Rule 9). |
| **Adverse Selection** | Microstructure phenomenon where passive limit orders are preferentially filled when toxic order flow moves against the trader (the core hazard of Track 1 locked circuits). |

---

## 5. Discrete 8-State Execution Architecture (Rule 4)

1. **`BROKER_INELIGIBLE`:** Blocked before submission by broker RMS (e.g. BTST prohibited on T2T/GSM/ASM, margin deficit, unauthorized DDPI).
2. **`REJECTED`:** Rejected upon receipt by the exchange matching engine (e.g. price outside circuit band, lot size error).
3. **`ACCEPTED`:** Order validated and recorded in exchange matching engine.
4. **`QUEUED`:** Order resting behind $R$ shares in the FIFO price-time queue ($V_{cum} < R$). Fill probability $= 0.0\%$.
5. **`PARTIAL`:** Cumulative contra turnover matches a portion of order size ($R < V_{cum} < R + Q_{order}$).
6. **`FILLED`:** Cumulative contra turnover exhausts queue rank and full order size ($V_{cum} \ge R + Q_{order}$).
7. **`EXPIRED_OR_CANCELLED`:** Order reaches end of trading session or is manually canceled without complete fill.
8. **`LOCKED_NO_BID` / `LOCKED_NO_COUNTERPARTY`:** Order book depth on opposite side is zero. Continuous trading halted; fill probability strictly $\equiv 0.0\%$.

---

## 6. Institutional Governance & Protocol

| Term | Institutional Definition |
| :--- | :--- |
| **The Gate (Rule 1)** | Mandatory paper-trading milestone: **60 prospective sessions** and **20 realistically fillable entries** with verified positive net expectancy before live capital is permitted. |
| **Paper Trading** | Rigorous simulated execution under live market conditions with zero real capital deployment. |
| **Prospective Logging** | Recording trade setups, entry orders, stops, and targets *prior* to session outcome. Retrospective entries are strictly excluded from gate counts. |
| **Expectancy ($E$)** | Net mathematical expected return per trade: $E = (\text{Win Rate} \times \text{Avg Win}) - (\text{Loss Rate} \times \text{Avg Loss}) - \text{Costs}$. Must be positive. |
| **CHALLENGE** | Formal written technical or quantitative objection logged in `shared/04_OPEN_QUESTIONS.md`. Required before modifying core architecture. |
| **Q1–Q16** | Master registry of open architectural and empirical questions tracked across the tri-agent desk. |
| **Provenance Taxonomy** | Classification of data: `MEASURED` (verified exchange data), `DERIVED` (mathematically computed), `ASSUMED` (unverified prior/heuristic). |
| **Red Team** | The adversarial auditing role (owned by Claude) tasked with breaking models, discovering edge-case flaws, and constructing counterexamples. |
| **Rule 11 Track Isolation** | Absolute separation of Track 1 (ESM micro-caps) from Track 2 (Liquid F&O momentum). Cross-track contamination is strictly prohibited. |

---

## 7. Software Engineering & Testing Terminology

| Term | Institutional Definition |
| :--- | :--- |
| **Fail-Closed** | Safety architecture where any missing, corrupted, or unverified data results in immediate rejection (`Pass = False`), aborting the trade. |
| **Fail-Open** | Dangerous flaw where missing or invalid data bypasses checks, permitting unauthorized execution. Prohibited across all models. |
| **NaN (Not a Number)** | IEEE 754 floating-point representation of missing numeric data. All comparison operations (`<`, `>`, `==`) against NaN evaluate to `False`, creating dangerous fail-open bugs if unhandled. |
| **Harness** | Automated adversarial test suite (`claude/models/track2_redteam_harness.py`) executing stress attacks A1–A10 against quantitative engines. |
| **Attacks A1–A10** | Claude's 10 red-team attacks testing math, boundary limits, float issues, degenerate stops, and relaxation logic. |
| **Screener Relaxation** | Automatic easing of screening filters when too few securities qualify. Must never fire when candidate pool is smaller than default targets. |
| **`min_surviving_pool`** | Threshold parameter controlling relaxation. Fixed in `liquid_momentum_screener.py` to `Optional[int] = None`, defaulting to `min(len(candidates), 15)`. |
| **SHA-256** | Cryptographic hash digest verifying the exact byte integrity and version identity of repository source files. |

