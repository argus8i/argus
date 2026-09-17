# Track 1 Market Mechanics: Micro-Cap Circuit & Surveillance Architecture

**Scope:** BSE/NSE Micro-Caps (Market Cap < ₹500 Cr, Price ≥ ₹10.00 floor) under exchange surveillance (ESM Stage 1/2, GSM, ASM, Trade-to-Trade).

---

## 1. Absolute Price Floor & Tick Arithmetic
- **The ₹10.00 Price Floor (Rule 2):** Any security trading below ₹10.00 is unconditionally disqualified. At sub-₹10 prices (e.g. ₹0.82 or ₹1.32), tick size distortion (1.22% or 0.76% per tick), near-permanent surveillance entrapment, and fatal liquidity evaporation destroy mathematical edge.
- **Inward Tick Truncation (BSE Consolidated Master Circular Item 1.6):**
  - Rule 1: A lower price-band value cannot be negative; applicable tick size shall be taken as the minimum possible value.
  - Rule 8: Computed higher and lower limits are floored/truncated inward to keep the traded range strictly within the percentage band.
  - *Example:* On CCDL close ₹1.32, $1.32 \times 1.05 = 1.386$. The upper circuit is truncated inward to ₹1.38 (not rounded up to ₹1.39, which would breach +5%).

---

## 2. Exchange Surveillance Frameworks (ESM / GSM / ASM)
- **Primary Source Citations & URLs:**
  - **NSE ESM Framework & FAQ v1.1:** [NSE Reports ESM](https://www.nseindia.com/reports/esm)
  - **Joint Revision Circulars:** BSE Notice 20230718-46 and NSE Circular NSE/SURV/57609 (effective 24 July 2023), establishing PCAS for ESM Stage 2 across all trading days, as amended by NSE Circulars 63361, 64066, and 64400 (2024).
  - **PCAS Timing Architecture (SEBI CIR/MRD/DP/6/2013 & CIR/MRD/DP/38/2013):**
    - Published at [NSE Periodic Call Auction](https://www.nseindia.com/static/products-services/equity-market-periodic-call-auction).
    - Operates six 1-hour sessions during regular market hours (09:30–10:30, 10:30–11:30, 11:30–12:30, 12:30–13:30, 13:30–14:30, 14:30–15:30).
    | Sub-phase | Duration | Rule |
    |---|---|---|
    | Order entry / modification / cancellation | 45 minutes | Orders can be entered, modified, or cancelled |
    | Random closure | Within last 1 min (44th–45th min) | System-driven random stop |
    | Order matching + confirmation | 8 minutes | Single equilibrium price discovery |
    | Buffer / transition | 7 minutes | Close current session, prepare next |
  - **Queue Allocation at ±2% Limit:** Price-time priority (FIFO). Limit orders carry over into subsequent auction sessions within the trading day (per para 2.8 of SEBI PCA framework).
- **Inward Tick Truncation (BSE Consolidated Master Circular Equity Segment Item 1.6):**
  - Specifies that lower price-bands cannot be negative (Rule 1) and computed limits are floored/truncated inward to tick size (Rule 8) to prevent exceeding percentage bands.

---

## 2.1 Broker Execution & Settlement Realism (Zerodha RMS & Statutory Architecture)
- **T2T Selling Mechanics (T+1 Allowed, Same-Day Barred):**
  - Per official Zerodha documentation ([What are Trade-to-Trade stocks?](https://support.zerodha.com/category/trading-and-markets/trading-faqs/general/articles/what-are-trade-to-trade-stocks)), securities bought on Day T under Trade-to-Trade (`BE`, `T`, `XT`) **can be sold on the next trading day (T+1)**.
  - What is strictly prohibited is **same-day intraday selling (BTST on Day T)**. Selling on T+1 is permitted from CNC holdings under T+1 settlement.
- **Proceeds Reuse & Early Pay-In (EPI):**
  - Under SEBI circular SEBI/HO/MIRSD/DOP/CIR/P/2020/143 and Zerodha RMS rules effective June 2023, sale proceeds from T2T holdings cannot be reused for new equity or intraday purchases on the same day until settlement completes.
- **CDSL TPIN vs. DDPI Authorization:**
  - Selling shares requires demat debit authorization. Non-DDPI clients must authorize sales via CDSL TPIN + OTP (available daily after 7:00 AM IST). Clients with active DDPI execute without TPIN.
- **Peak Margin Regime:**
  - Cash segment trades require upfront VaR+ELM margin (minimum ~20%) per SEBI circular SEBI/HO/MRD2/DCAP/CIR/P/2020/127. Where Early Pay-In (EPI) of securities is executed, separate upfront peak margin is not debited.
- **Short-Delivery & Clearing Corporation (ICCL/NCL) Auction Penalties:**
  - In Trade-to-Trade scrips, seller delivery default cannot be squared off intraday or borrowed via SLB. The Clearing Corporation conducts a buy-in auction. If no sellers are found in auction, mandatory close-out occurs at the higher of:
    1. Highest trade price from trade day to auction day, or
    2. 20% over the official closing price on the auction day, plus a 0.05% valuation penalty.

---

## 3. The Lower-Circuit Liquidity Trap & Exit Dynamics
- **Zero-Bid Lockout:** When a micro-cap hits Lower Circuit, bid depth drops to 0 while millions of shares queue on the sell side. Stop-loss market orders cannot execute because counterparty liquidity $\equiv 0\%$.
- **Empirical Calibration (CROPSTER):**
  - Primary Descent (23-Jul to 05-Aug): Exactly **10 consecutive sessions** closing at the 5% LC limit (₹7.64 → ₹4.61, −39.66% unfillable drawdown).
  - Secondary Descent (25-Aug to 04-Sep): **9 consecutive sessions** (₹4.55 → ₹2.91, −36.04%).
- **Rule 5 Risk Calibration:**
  $$\text{Max Position Size} = \frac{\text{Rupees Willing to Lose Outright}}{0.401}$$
  *Never size assuming a stop loss will execute when bid depth is zero. Standardized strictly to $1 - 0.95^{10} = 40.126\%$ (0.401).*
- **Rule 9 Liquidity & Participation Gate (Calm-Market Participation Filter):**
  $$\text{Daily Fill Fraction} = \min\left(1.0, \frac{0.15 \times \text{Daily Volume}}{\text{Position Shares}}\right)$$
  $$\text{Sessions to Exit} = \frac{\text{Position Shares}}{0.15 \times \text{Daily Volume}} \le 2.0 \text{ sessions}$$
  *A position must never require more than 2 sessions to exit at 15% maximum market participation.*
  - **Calm-Market Filter vs. Tail Risk (Fix Claude F4):** Rule 9 is strictly a calm-market sizing filter to prevent dominating normal two-sided volume. During an unbroken lower-circuit crash (Rule 5 crisis), daily volume collapses to near-zero ($V \to 0$), meaning clearable capacity completely evaporates. Rule 9 cannot bound tail losses during zero-bid lockouts; tail risk is bounded exclusively by Rule 5 outright loss sizing.

---

## 4. Execution & Exit Protocol (Rule 7 Pre-Circuit Accumulation)
- **Entry Rules:** Buy ONLY during two-sided accumulation bases where:
  1. Price $\ge$ ₹10.00 floor (Rule 2).
  2. 20-day volume is expanding $\ge 3\times$.
  3. Spread is $< 1.0\%$.
  4. Daily range is $> 3.0\%$.
  5. Distance to Upper Circuit is $\ge 3$ ticks and $\ge 15\%$ of circuit band width.
- **Exit Strategy:** Pre-emptive profit exits (+15% to +20%) taken **into the Upper Circuit buyer queue on Day 3 or Day 4** while resting buyer depth is massive. Never attempt to hold for a reversal candle.
- **Rule 6 Override:** Any band tightening ($20\% \to 10\%, 10\% \to 5\%, 5\% \to 2\%$) or surveillance flag triggers an immediate freeze and mandatory exit into the earliest available liquidity.

