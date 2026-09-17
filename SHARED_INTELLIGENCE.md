# Shared Intelligence: Indian Circuit Microstructure & Operator Patterns

This knowledge base contains verified observations, mathematical realities, and microstructure mechanics derived from live BSE/NSE market data. All agents (**Antigravity**, **Claude**, **ChatGPT**) must consult and contribute to this file.

---

## 1. The Circuit Architecture on BSE / NSE

1. **Circuit Limits**:
   - Micro-caps and SME stocks typically have circuit filters of **±5%** or **±2%** (sometimes ±10% / ±20% for non-trade-to-trade stocks).
   - Once the price touches the circuit limit, no trades can occur outside that price band for the remainder of the session (unless dynamic circuits apply on F&O stocks, which penny stocks never have).

2. **Order Execution Microstructure (Price-Time Priority / FIFO)**:
   - When a stock is at **Upper Circuit (UC)**:
     - Selling orders execute immediately against the highest bids.
     - Buying orders enter the back of the queue. If there are 50 lakh shares ahead of you, your buy order will not execute unless 50 lakh shares are sold by existing holders.
   - When a stock is at **Lower Circuit (LC)**:
     - Buying orders execute immediately.
     - Selling orders enter the back of the offer queue.
     - **The Liquidity Trap**: If Total Bids = 0, **zero trades execute**. Your sell order sits untouched all day.

3. **Pre-Market Session (09:00 AM - 09:08 AM)**:
   - **09:00 - 09:07 AM**: Order collection and modification window.
   - **09:07 - 09:08 AM**: Matching and price discovery (no modification or cancellation allowed).
   - **Operator Behavior**: Operators place multi-million share limit buy orders at the maximum permitted price (+5%) during pre-market to anchor the equilibrium price at UC. Retailers see this and submit AMO/market orders, increasing the queue.

---

## 2. The 5-Stage Operator Lifecycle

Based on empirical charts from `CROPSTER`, `CHANDRIMA`, and `CCDL`:

| Stage | Name | Technical Signs | Order Book Depth | Strategy Action |
| :--- | :--- | :--- | :--- | :--- |
| **0** | Accumulation | Flat price, dormant volume, narrow ranges over weeks. | Low bids, low offers, thin depth. | Track on watchlist; do not enter yet. |
| **1** | Breakout Surge (Day 1-2) | Price gaps to UC; volume explodes >5x - 10x 20-day SMA. | Massive bid depth (>10x offer); zero offers remaining. | **PRIMARY ENTRY WINDOW**. Enter via AMO limit at UC. |
| **2** | Parabolic Run (Day 3-4) | Consecutive gap-up circuits; flat horizontal candles. | Bids consistently in millions; zero sellers. | **HOLD & PREPARE EXIT**. Trailing profit locked. |
| **3** | Distribution (Day 4-6) | Heavy churn; circuit breaks intraday; high upper wicks. | Bids begin declining; offers start appearing rapidly. | **PRE-EMPTIVE EXIT**. Sell into UC bid queue. |
| **4** | The Dump / Freeze (Post-Peak)| Gap down straight to LC; horizontal red dashes. | **Total Bids = 0**. Millions trapped on offer. | **DO NOT BUY**. If trapped, place AMO sell order at 15:45:01 PM. |

---

## 3. Catastrophic Risk: SEBI Surveillance Measures (ASM / GSM / ESM)

1. **Enhanced Surveillance Measure (ESM)**:
   - **Stage I**: 100% margin, price band capped at 5% or 2%.
   - **Stage II**: Shifted to **Periodic Call Auction** (trading only once every hour or once per day!). Volume collapses to zero.
---

## 4. Empirical Case Study: The 1-Hour Exit in a 45-Lakh Queue (CROPSTER)

- **Scenario:** User placed an exit order for 12,560 shares (~₹60,000) into an active Lower Circuit queue of 40–45 lakh shares on offer. The order executed fully within **1 hour**.
- **The Liquidity Turnover Principle:** Market depth only displays *unmatched resting limit orders*. If daily volume turnover is 1.5 Cr to 2.7 Cr shares, approximately 20 to 50 lakh shares are changing hands every hour. Even in a lower circuit, small buy orders arrive continuously (from operators recycling float or bargain hunters) and absorb orders in FIFO queue order.
- **The 09:00 AM vs 09:15 AM Timing Edge:**
  - Orders placed at **09:15:00 AM** sit behind thousands of retail pre-market orders.
  - An exit decision executed during pre-market (**09:00 - 09:05 AM**) or via **AMO at 15:45 PM** secures priority in the front of the queue. However, execution requires incoming turnover ($V_{cum} \ge R$). If counterparty bids remain zero, queue priority yields 0% fills (`LOCKED_NO_BID`).

---

## 5. The 20% Target vs 40% Greed Rationale

- Most operator rallies in these stocks reach +30% to +40% before reversing.
- **The Winning Formula:** By fixing the target at **+20% (Day 4 exit)**:
  1. We exit during **Peak Buyer Euphoria** when resting buy orders are at their maximum (crores of shares on bid).
  2. A market/limit sell order placed into a multi-crore buyer queue has very high fill probability ($P(\text{Fill}) \approx 100\%$) because buyers are waiting to absorb available shares.
  3. We completely avoid the unpredictable distribution phase where the operator suddenly pulls the bids and drops the stock into a zero-bid Lower Circuit lock.

