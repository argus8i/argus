# Monday Prospective Paper-Trading Watchlist (2026-09-14)

**Observation Session:** Monday, 14-September-2026  
**Source Data:** Official BSE Capital Market Bhavcopy (Friday Close: 2026-09-11)  
**Strategy Rule:** AGENTS.md Rule 7 (Pre-Circuit Accumulation Breakout)  
**Execution Mode:** STRICT OBSERVATION ONLY (AGENTS.md Rule 1 — Real Capital Prohibited)  
**Paper Gate Milestone Counter:** **0 / 60 Sessions | 0 / 20 Fills**  

---

## 1. Top 5 Prospective Candidates Meeting All 11 Execution Gates

| Scrip Code | Symbol | Group | Close (Rs) | Traded Volume | Vol Expansion | Daily Range | Distance to UC | Max Paper Sizing (Rule 5 & 9) |
|---|---|---|---|---|---|---|---|---|
| **544305** | One Mobikwik Systems Limited | `B` | ₹209.55 | 892,396 sh | **21.0x** | 14.22% | **11.21% headroom** | **59 shares** (`CAPITAL_RISK_RULE_5`) |
| **533343** | LOVABLE LINGERIE LTD. | `B` | ₹72.16 | 29,989 sh | **20.2x** | 6.27% | **19.96% headroom** | **172 shares** (`CAPITAL_RISK_RULE_5`) |
| **544497** | ANLON HEALTHCARE LIMITED | `B` | ₹19.52 | 6,881,225 sh | **15.5x** | 14.16% | **7.94% headroom** | **638 shares** (`CAPITAL_RISK_RULE_5`) |
| **533056** | VEDAVAAG SYSTEMS LTD. | `B` | ₹23.36 | 224,892 sh | **13.3x** | 13.89% | **14.64% headroom** | **533 shares** (`CAPITAL_RISK_RULE_5`) |
| **500240** | KINETIC ENGINEERING LTD. | `XT` | ₹229.35 | 267,625 sh | **9.9x** | 10.25% | **6.55% headroom** | **54 shares** (`CAPITAL_RISK_RULE_5`) |

---

## 2. Invariant Execution Gates & Rules for Each Candidate

### A. Pre-Open Order Entry Rules (09:00:00 – 09:05:00 IST)
- **Discrete 4-State Fill Check:** If the stock opens locked at Upper Circuit with 0 offers, **FILL PROBABILITY = 0% (`LOCKED_NO_BID`)**. Order must be rejected; locked circuits must NEVER be chased (Rule 3).
- **Two-Sided Liquidity Check:** Order entry allowed only if bid-ask spread is verified $< 1.0\%$ and both bids and offers exist.

### B. Position Sizing & Downside Risk Budget (Rule 5 & Rule 9)
- **Risk Allocation:** ₹5,000 outright maximum rupee loss willingness.
- **10-Day Lower-Circuit Lockout Calibrated Sizing:**
  $$\text{Capital Sizing} = \frac{\text{₹5,000}}{0.401} = \text{₹12,468.83}$$
- **15% Volume Participation Cap:** Position shares cannot exceed $2 \times 0.15 \times \text{Daily Volume}$.

### C. Day 3 / Day 4 Pre-Emptive Profit Exit Protocol (Rule 7)
- Target pre-emptive profit exits ($+15\%$ to $+20\%$) taken into the Upper Circuit buyer queue on Day 3 or Day 4.
- **Rule 6 Surveillance Pre-emption Override:** If the exchange revises the circuit band ($20\% \to 10\%, 10\% \to 5\%, 5\% \to 2\%$) or flags the scrip under ESM/GSM/T2T, **EXIT IMMEDIATELY** into earliest available liquidity. Do NOT wait for Day 3/4 targets.

---

## 3. Mandatory Paper-Trading Logging Protocol
- Every prospective order and fill must be logged in `CHATGPT/observation_log.csv` and `shared/03_TRADE_LOG.md`.
- Minimum milestone: **60 prospective sessions and 20 fillable paper trades** before any live capital can be reviewed.