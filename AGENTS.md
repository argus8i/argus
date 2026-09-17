# Project Swing Trades: Autonomous Agent Ground Rules

These rules apply unconditionally to all AI agents (**Antigravity**, **Claude**, **ChatGPT**) collaborating in `c:\Users\yashw\swing trades`.

---

## 1. Mandatory Paper-Trading Gate (Observation Only)
- **Constraint:** Real capital deployment is strictly prohibited.
- **Milestone:** The system must complete a minimum of **60 prospective trading sessions** and log at least **20 realistically fillable entries** in `CHATGPT/observation_log.csv` and `shared/03_TRADE_LOG.md` with verified positive net expectancy before any live trading is considered.

## 2. Absolute ₹10.00 Price Floor
- **Constraint:** Immediate disqualification of any security trading below **₹10.00**.
- **Rationale:** Sub-₹10 securities suffer from extreme tick-size distortion (at ₹0.82, one tick is 1.22%; at ₹1.32, it is 0.76%), near-permanent surveillance entrapment, and fatal liquidity evaporation.

## 3. Prohibition of Locked-Circuit Chasing
- **Constraint:** Never submit or recommend a buy limit order for a stock locked at Upper Circuit where offer quantity is zero or negligible.
- **Rationale:** Fills on locked upper circuits occur only when the operator is distributing ("buying the exit"). Fill probability and forward returns are negatively correlated by construction.

## 4. Discrete 4-State Execution Modeling
- **Constraint:** Never assume deterministic or "guaranteed" fills (e.g. "ensures fill within 15–60 minutes").
- **Required Architecture:** All simulators, backtests, and paper logs must model:
  1. `LOCKED_NO_BID`: Total Bids == 0 (or Total Offers == 0 on UC). Fill probability $\equiv 0\%$.
  2. `QUEUED`: Order accepted, waiting behind $R$ shares in FIFO queue.
  3. `PARTIAL`: Incoming turnover matches a portion of order quantity.
  4. `FILLED`: Cumulative volume turnover exceeds queue rank + order size ($V_{cum} \ge R + Q_{order}$).

## 5. 10-Day Lower-Circuit Risk Calibration
- **Constraint:** Position sizing must assume an unbroken exit lockout of **10 consecutive lower-circuit sessions** ($-40.1\%$ loss), calibrated from CROPSTER's verified descent. Stop-losses must never be assumed to execute when bid depth is zero.
- **Formula:**
  $$\text{Max Position Size} = \frac{\text{Rupees Willing to Lose Outright}}{0.401}$$
  *(Calibrated strictly to 10 consecutive 5% lower-circuit sessions: $1 - 0.95^{10} = 40.126\% \approx 0.401$).*

## 6. Surveillance Pre-Emption & Daily Band Monitoring
- **Constraint:** Run daily pre-open checks comparing today's circuit band against yesterday's using `antigravity/models/band_revision_monitor.py`.
- **Trigger:** Any band tightening ($20\% \to 10\%, 10\% \to 5\%, 5\% \to 2\%$) or classification under ESM Stage 1/2, GSM, ASM, or Trade-to-Trade (`BE`) triggers an immediate freeze and mandatory exit review.

## 7. Pre-Circuit Accumulation Only (Rule 6 Setup)
- **Constraint:** Buy only during two-sided accumulation bases where 20-day volume is expanding $\ge 3\times$, spread is $< 1\%$, daily range is $> 3\%$, and a valid stop-loss can be placed.
- **Exit Strategy:** Target pre-emptive profit exits ($+15\%$ to $+20\%$) taken into the Upper Circuit buyer queue on Day 3 or Day 4.

## 8. Tri-Agent Consensus Protocol
- **Constraint:** Cross-agent peer review is mandatory before modifying core models or executing paper trades:
  - **Claude:** Microstructure, adverse-selection testing, and red-teaming.
  - **ChatGPT:** Filings, corporate actions, and surveillance tracking.
  - **Antigravity:** Quantitative modeling, execution automation, and Bhavcopy pipelines.

## 9. Liquidity & Market Participation Sizing Gate (Claude Specification)
- **Constraint:** Position size must never exceed **15% maximum participation** of realistic daily volume over a 2-session clearable horizon.
- **Formulas:**
  $$\text{Daily Fill Fraction} = \min\left(1.0, \frac{0.15 \times \text{Daily Volume}}{\text{Position Shares}}\right)$$
  $$\text{Sessions to Exit} = \frac{\text{Position Shares}}{0.15 \times \text{Daily Volume}} \le 2.0 \text{ sessions}$$
  $$\text{Max Combined Position Size} = \min\left(\frac{\text{Rupees Willing to Lose Outright}}{0.40}, 2 \times 0.15 \times \text{Daily Volume} \times \text{Price}\right)$$
- **Rationale:** At CHANDRIMA's 10-Sep volume of 6,355 shares, a 4,500-share position represented **70.8% of the entire day's turnover**. At that participation, you are not trading into the market; you ARE the market, creating your own adverse-selection liquidity trap.

## 10. Strict Precedence Hierarchy of Autonomous Execution Gates
- **Constraint:** When market events cause rules to fire on overlapping states with conflicting instructions, the following strict hierarchy governs:
  1. **Rule 1 (Observation Only):** 100% Cash; zero real capital.
  2. **Rule 6 (Surveillance Pre-emption & Band Cut):** Immediate freeze and mandatory exit. **Strictly overrides Rule 7.** If a stock enters via Rule 7 on Day 1, but receives a band cut ($20\% \to 10\%, 10\% \to 5\%$) or surveillance flag on Day 2/3, Rule 6 mandates an immediate exit into the earliest available liquidity. Holding to wait for Day 3/4 targets is prohibited.
  3. **Rule 2 (Absolute ₹10.00 Floor):** Immediate disqualification.
  4. **Rule 9 (Liquidity Participation Gate):** Rejection of any trade requiring $>2$ sessions to exit at $\le 15\%$ participation.
  5. **Rule 3 & 4 (No Locked UC Chasing & Discrete Execution):** Never chase locked circuits; model non-deterministic fills.
  6. **Rule 7 (Pre-Circuit Accumulation Breakout):** Entry allowed only when Rules 1–6 and Rule 9 pass.

## 11. Absolute Track Isolation (ESM Micro-Caps vs. Liquid Short-Term)
- **Constraint:** All agents must unconditionally treat **Track 1 (ESM / Circuit Micro-Caps)** and **Track 2 (Liquid High-Beta Short-Term Momentum)** as two independent, decoupled quantitative systems. Cross-track contamination of rules, watchlists, logs, or sizing is strictly prohibited.
- **Track 1 Guardrails (ESM & Circuit Micro-Caps):**
  - Governed by Rules 2, 3, 4, 5, 6, 7, 9, 10.
  - Applies exclusively to micro-caps ($\text{Mcap} < \text{₹500 Cr}$) under fixed circuit bands (2%, 5%) and surveillance (ESM Stage 1/2, PCAS, T2T).
  - Never assume continuous liquidity or deterministic stop-loss execution. Sizing is governed by Rule 5 (10-day LC lockout) and Rule 9 (15% volume cap).
  - Dedicated storage: `shared/track1_esm/` and `CHATGPT/observation_log.csv`.
- **Track 2 Guardrails (Liquid High-Beta Short-Term Momentum):**
  - Governed by 15-minute Opening Range Breakout (ORB), dynamic flexing price bands (NSE/FAOP/62241), continuous two-sided liquidity, and strict rupee risk budgeting (₹1,500/trade).
  - Applies exclusively to active F&O underlyings (`EQ` series, Mcap ₹4,000–₹75,000 Cr, DTV $\ge$ ₹30 Cr).
  - **Surveillance Boundaries & Safeguards:** ESM Stage 1/2 and PCAS auction restrictions do not apply to Track 2 (ESM is bounded to Mcap < ₹1,000 Cr). However, general ASM/GSM screening and daily F&O-membership verification remain mandatory (`is_surveillance: False` and `is_fno_underlying: True` enforced fail-closed in engine). If a scrip exits F&O or enters ASM/GSM, it is immediately disqualified from Track 2.
  - Strictly prohibited from applying Track 1 circuit-freeze paranoia, ESM surveillance restrictions, or 10-day LC sizing to liquid F&O underlyings.
  - Strictly prohibited from applying Track 2 continuous stop-loss assumptions to Track 1 micro-caps.
  - Dedicated storage: `shared/track2_liquid/` and `CHATGPT/monday_orb_paper_template.csv`.

