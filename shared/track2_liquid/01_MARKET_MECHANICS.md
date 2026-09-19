# Track 2 Market Mechanics: Liquid High-Beta Momentum Architecture

**Scope:** Liquid Midcap 150 / Smallcap 250 (`EQ` Series) active F&O Underlyings trading in continuous two-sided continuous market.

---

## 1. Structural Difference from Track 1
Track 2 is fundamentally decoupled from micro-cap circuit trading:
- **No Fixed 5% Circuit Freezes:** Because all candidate scrips are active F&O underlyings, the National Stock Exchange (NSE) applies **dynamic flexing price bands** (circular NSE/FAOP/62241). When price reaches the band threshold, a cooling period triggers and the band flexes outward. Order books remain two-sided with continuous multi-crore liquidity.
- **Continuous Bid/Offer Depth:** Daily traded turnover ranges from ₹50 Crore to ₹500+ Crore, ensuring orders can fill and exit without creating adverse-selection traps.
- **Tail Risk Reality:** While continuous liquidity eliminates the zero-bid locked circuit trap, tail risk is NOT zero: an overnight gap-down (e.g., -10% on earnings or geopolitical shock) can still bypass an intraday stop.

---

## 2. The 15-Minute Opening Range Breakout (ORB) Strategy
- **The Opening Range Window (09:15 – 09:30 IST):**
  - The high and low of the first 15-minute candle establish the day's baseline:
    - $\text{OR High} = \max(\text{High}_{09:15-09:30})$
    - $\text{OR Low} = \min(\text{Low}_{09:15-09:30})$
- **Entry Qualification Criteria (09:30 – 14:30 IST):**
  1. **Price Breakout:** Current Price > $\text{OR High}$.
  2. **Volume Confirmation:** 15-minute bucket volume $\ge 2.5\times$ historical 15-minute median volume for that time slot.
  3. **Extension Ceiling Guard:** Current Price must NOT exceed $\text{OR High} + (0.5 \times \text{ATR14})$. Breakouts beyond this ceiling are rejected as `HOLD_REJECT_OVEREXTENDED` (chase risk).
  4. **Degenerate Stop Guard:** $\text{Entry Price} > \text{OR Low}$. If $\text{Entry Price} \le \text{OR Low}$, the trade is strictly rejected (`REJECTED_DEGENERATE_STOP`).

---

## 3. Strict Rupee Risk Budget Sizing
Position sizing is strictly governed by a fixed rupee loss budget (e.g. ₹1,500.00 willing to risk), completely eliminating arbitrary share guessing:

$$\text{Stop Price} = \max\left(\text{OR Low}, \text{Entry Price} - (1.5 \times \text{ATR14})\right)$$
$$\text{Risk per Share} = \text{Entry Price} - \text{Effective Exit Price}$$
$$\text{Quantity} = \min\left(\left\lfloor \frac{\text{Risk Budget}}{\text{Risk per Share}} \right\rfloor, \left\lfloor \frac{\text{Max Notional Ceiling}}{\text{Entry Price}} \right\rfloor, \left\lfloor \frac{0.001 \times \text{DTV Rupees}}{\text{Entry Price}} \right\rfloor\right)$$

- **Default Risk Budget:** ₹1,500.00 per trade.
- **Max Notional Ceiling:** ₹1,00,000.00.
- **Turnover Participation Cap:** Maximum 0.1% of 20-day median daily turnover.

---

## 4. Stop-Loss Execution & R:R Reality (SL-M vs. SL-Limit)
- **Scenario A: True Zero-Offset SL-M (Target State on NSE Cash):**
  - Order type: Stop-Loss Market (`SL-M`).
  - Effective Exit = Stop Price.
  - $\text{Target Price} = \text{Entry} + 2.0 \times (\text{Entry} - \text{Stop})$.
  - $\text{Realized R:R} = \mathbf{1 : 2.00} \implies \text{Breakeven Win Rate} = \mathbf{33.3\%}$.
  - *Status:* Pending Yashu's 30-second live Kite verification (Q13).
- **Scenario B: SL-Limit with 0.5% Buffer (Current Conservative Baseline):**
  - Order type: Stop-Loss Limit (`SL-LIMIT`).
  - Limit Exit Price = $\text{Stop Price} \times (1 - 0.005)$.
  - Effective Exit = Limit Exit Price.
  - $\text{Risk per Share} = \text{Entry} - \text{Effective Exit} = \text{Entry} - \text{Stop} \times 0.995$.
  - **Dynamic Realized R:R Curve:** Because Target is anchored to the structural stop ($2 \times (\text{Entry} - \text{Stop})$) while risk divides by effective exit, realized R:R is a function of stop width $w = (\text{Entry} - \text{Stop}) / \text{Entry}$:
    $$\text{Realized R:R}(w) = \frac{2w}{w + 0.005 \times (1 - w)}$$
    - **Tight Stop ($w = 0.8\%$):** Realized R:R = **1 : 1.235** (Breakeven Win Rate = **44.7%**).
    - **Standard Stop ($w = 1.0\%$):** Realized R:R = **1 : 1.338** (Breakeven Win Rate = **42.8%**).
    - **Moderate Stop ($w = 1.5\%$):** Realized R:R = **1 : 1.506** (Breakeven Win Rate = **39.9%**).
    - **Anchor Point ($w \approx 1.67\%$):** Realized R:R = **1 : 1.552** (Breakeven Win Rate = **39.2%** — conservative baseline anchor).
    - **Wide Stop ($w = 2.0\%$):** Realized R:R = **1 : 1.606** (Breakeven Win Rate = **38.4%**).
    - **Volatile Stop ($w = 3.0\%$):** Realized R:R = **1 : 1.722** (Breakeven Win Rate = **36.7%**).
    - **Wide Swings ($w = 5.0\%$):** Realized R:R = **1 : 1.826** (Breakeven Win Rate = **35.4%**).
  - *Engine Status:* `calculate_position_size()` dynamically computes the exact realized R:R for each trade; 1:1.55 is solely the midpoint reference anchor at ~1.67% stop width.

---

## 5. Trade Horizons & Exits (Two-Tranche Model V2.0)
To eliminate premature trade churn caused by moving 100% of a position to breakeven, V2.0 automatically partitions every qualified entry into two distinct tranches:
1. **Tranche 1 (50% Shares, rounded up):** Fixed +1.5R to +2.0R Profit-Banking Tranche.
   - When price reaches $+1.0R$, stop moves to breakeven (`entry_price`).
   - When price reaches Target 1 ($+1.5R$), Tranche 1 executes and locks in guaranteed gross profit.
2. **Tranche 2 (50% Shares, rounded down):** Multi-Day CNC Swing Runner.
   - Once Tranche 1 reaches $+1.0R$ or hits Target 1, Tranche 2 stop is raised to breakeven (`entry_price`). Downside risk on the total trade becomes ₹0.00.
   - Tranche 2 then trails dynamically on:
     $$\text{Stop}_{\text{Tranche 2}} = \max(\text{Entry Price}, \text{Previous Day's Low}, \text{Peak Price} - 1.5 \times \text{Daily ATR})$$
   - Allows runners (e.g. `CDSL`) to capture $+15\%$ to $+30\%$ multi-week trend expansions without choking on intraday pullbacks.

---

## 6. Market Regime & Breadth Filter (`MarketRegimeFilter`)
Before admitting any long Opening Range Breakout at 09:30 IST, the engine evaluates broader market conditions:
1. **Index Trend:** Nifty 50 15-minute Opening Range (09:15–09:30 IST):
   - $\text{Nifty LTP} < \text{Nifty OR Low} \implies$ `DISTRIBUTION_GATED`: All long breakouts are immediately rejected (`HOLD_REJECT_MARKET_DISTRIBUTION`).
2. **Advance/Decline Breadth:**
   - $\text{A/D Ratio} < 1.0 \implies$ `DISTRIBUTION_GATED`: Negative market breadth aborts long entries.
   - $\text{A/D Ratio} \ge 1.20$ and $\text{Nifty} > \text{OR High} \implies$ `BULLISH_EXPANSION`: Standard $2.5\times$ volume confirmation active.
   - Neutral / inside range $\implies$ `NEUTRAL_SELECTIVE`: Elevated $3.5\times$ volume confirmation required.

---

## 7. Dynamic Universe Discovery & Circular Automation
1. **Dynamic 09:15 IST Pre-Market Scanner (`Track2UniverseScanner`):**
   - Ranks the most volatile, active F&O underlyings by:
     $$\text{Score} = \left(\frac{\text{Pre-Open Volume}}{\text{10D Median Pre-Open Volume}}\right) \times \text{Beta} \times \left(1 + \frac{|\text{Gap \%}|}{10}\right)$$
   - Emits the top 8 candidates to `shared/track2_liquid/dynamic_universe.json`.
   - Fail-Closed Fallback: Automatically falls back to canonical Baskets A & B if dynamic pool is $< 4$ scrips.
2. **Automated 19:00 IST Exchange Circular Poller (`ExchangeCircularPoller`):**
   - Automatically ingests daily BSE/NSE ASM/GSM and F&O inclusion/exclusion bulletins every evening.
   - Guarantees zero scrips under surveillance or outside derivatives enter Track 2, enforcing 100% compliance 14 hours before market pre-open.

