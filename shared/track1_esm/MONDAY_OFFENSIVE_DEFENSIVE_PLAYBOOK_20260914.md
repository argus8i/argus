# Monday Tactical Offense + Defense Execution Playbook (2026-09-14)

**Execution Phase:** Pre-Open Observation & Auction Matching (09:00:00 - 09:15:00 IST)  
**Operating Mode:** STRICT OBSERVATION ONLY (AGENTS.md Rule 1 — Real Capital Prohibited)  
**Desk Milestone Counter:** **0 / 60 Sessions | 0 / 20 Realistically Fillable Entries**  

---

## 1. Dual-Core Evaluation Matrix (Defense + Offense)

| Scrip Code | Symbol | Group | Friday Close | Sizing (Rule 5 & 9) | Max Loss Airbag | Offense 1: Pre-Open Auction Sniping | Offense 2: Delivery Absorption Footprint |
|---|---|---|---|---|---|---|---|
| **544305** | One Mobikwik Systems Ltd. | `B` | ₹209.55 | **59 shares** (₹12,363.45) | **₹4,957.74** (0.401 divisor) | **Limit: ₹212.71 (09:00:01 - 09:04:59 IST)** | **NORMAL_TRADING (70% deliv, 21.0x vol)** |
| **533343** | Lovable Lingerie Ltd. | `B` | ₹72.16 | **172 shares** (₹12,411.52) | **₹4,977.02** (0.401 divisor) | **Limit: ₹73.26 (09:00:01 - 09:04:59 IST)** | **NORMAL_TRADING (70% deliv, 20.2x vol)** |
| **544497** | Anlon Healthcare Ltd. | `B` | ₹19.52 | **638 shares** (₹12,453.76) | **₹4,993.96** (0.401 divisor) | **Limit: ₹19.83 (09:00:01 - 09:04:59 IST)** | **NORMAL_TRADING (70% deliv, 15.5x vol)** |
| **533056** | Vedavaag Systems Ltd. | `B` | ₹23.36 | **533 shares** (₹12,450.88) | **₹4,992.8** (0.401 divisor) | **Limit: ₹23.73 (09:00:01 - 09:04:59 IST)** | **NORMAL_TRADING (70% deliv, 13.3x vol)** |
| **500240** | Kinetic Engineering Ltd. | `XT` | ₹229.35 | **54 shares** (₹12,384.9) | **₹4,966.34** (0.401 divisor) | **Limit: ₹232.81 (09:00:01 - 09:04:59 IST)** | **STATUTORY_T2T_100 (100% deliv, 9.9x vol)** |

---

## 2. Action Plan: Sub-Second Pre-Open Execution (09:00:00 – 09:08:00 IST)

### A. Pre-Open Order Entry Rules (Module 1)
1. **09:00:01 IST Queue Placement:** Place limit buy order within the first 5 seconds to secure Queue Rank $R \le 10$.
2. **Equilibrium Tick Protection:** Never place market orders in pre-open. Limit price must be set 2 ticks above Indicative Equilibrium Price (IEP) and at least 3 ticks below Upper Circuit ceiling.
3. **Rule 3 Prohibition:** If Indicative Offers = 0 at the Upper Circuit at 09:07 IST, cancel order immediately. Zero contra liquidity means adverse selection trap.

### B. Delivery & Float Lockup Confirmation (Module 2)
1. **XT Series (Kinetic Engineering):** Statutory 100% gross delivery settled. 100% of volume locks into depository demat accounts; zero intraday short-selling or day-trading permitted.
2. **B Series (Mobikwik, Lovable, Anlon, Vedavaag):** Minimum 70%-85% delivery threshold enforced. Any candidate exhibiting volume expansion with < 40% delivery is flagged as **DISTRIBUTION CHURN** and disqualified.

### C. 3-Stage Laddered Exit Protocol (Offense)
- **Leg 1 (De-Risking):** Sell **33%** at $+10.0\%$ into two-sided continuous book on Day 2/3.
- **Leg 2 (Core Profit Target):** Sell **33%** at $+15.0\%$ pre-emptively on Day 3/4.
- **Leg 3 (Moonshot/Queue Runner):** Place resting limit sell at Upper Circuit price for final **34%** on Day 4. If displayed bids exceed $3.0\times$ volume, abort and exit at market bid to avoid spoof dump.

---

## 3. Mandatory Paper-Trading Logging Protocol
- All simulated orders, queue positions, and theoretical fills must be recorded in `CHATGPT/observation_log.csv` and `shared/03_TRADE_LOG.md`.
- Invariant: `counts_toward_paper_gate = false` until 60 full sessions and 20 fillable entries pass.