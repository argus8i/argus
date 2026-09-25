# Indian Liquid-Equity Alpha Incubation: Premise Audit, Breakeven Hurdle, and Ranked Testable Slate

**Audit Date:** 25 September 2026  
**Desk:** ARGUS 8i // BEACON Track 2 (Liquid High-Beta F&O Momentum / Cash Equity)  
**Status:** `OBSERVATION_ONLY` (Rule 1 Mandatory Paper Gate Active: 0/60 Sessions, 0/85 E2/E3 Fills, 100% Cash)  
**Consensus Protocol:** Red-Team Hardened (Claude Microstructure & Adverse-Selection, Codex Senior Reliability, Antigravity Quant & Execution)  

---

## Executive Summary & Core Verdict

A rigorous audit of the desk's operational and financial premises reveals that **none of the exploratory directive angles is ready for live capital deployment today**, and **three foundational premises previously treated as settled are factually incorrect**:

1. **Friction is not a flat 0.15%**:
   - **MIS Intraday** round-trip friction on a ₹58,333 slot is **₹61.86 to ₹61.99 (0.106%)** before slippage.
   - **CNC Overnight** round-trip friction is **₹144.40 to ₹144.52 (0.248%)** for a single-day exit, rising to **₹159.15 (0.273%)** when Tranche 1 and Tranche 2 are squared off across different calendar days.
2. **The ₹58,333 slot cap binds at 2.57%, not 2.10%**:
   - $\text{Binding Stop } S^* = \frac{₹1,500}{₹58,333.33} = 2.5714\%$.
   - For all stops tighter than 2.57%, 1R is **not** ₹1,500. A 0.50% stop yields $1R = ₹291.67$; a 1.00% stop yields $1R = ₹583.33$. Consequently, friction in R at a 0.5% stop is **0.21R (MIS)** and **0.50R (CNC)**.
3. **Tight stops do not create edge**:
   - In a 15-minute bar regime on NSE F&O equities ($\sigma_d \approx 2.5\%$, $\sigma_{15m} \approx 0.51\%$, expected bar range $\approx 0.80\%$), a 0.5% stop sits entirely inside the noise of a single continuous bar. Tightening a stop merely inflates nominal R multiples while dramatically raising the probability of an uninformative noise stop-out.

### Recommended Strategy Slate (Ranked by Evidence & Cost Survival)

| Rank | Strategy ID | Instrument & Side | Regime | Structural Invalidation | Cost Hurdle ($p^*$) | Academic / Empirical Provenance |
|---|---|---|---|---|---|---|
| **1** | **`PEAD_DRIFT` (S1)** | CNC Delivery (Long-only) | Idiosyncratic / Low-Vol | Close below Day-0 low (3–6% wide) | $p^* \approx 50.0\%$ (q=0.5) | Harshita, Singh & Yadav (2018), Nifty 500: +6.0% long-short spread over 64 days |
| **2** | **`SWEEP_RECLAIM` (S2)** | MIS Intraday (Both sides) | Range / Choppy ($ADX_{5d} < P_{40}$) | Sweep low − 1 tick (0.5%–1.5% wide) | $p^* \approx 44.4\%$ (q=0.5) | Heston, Korajczyk & Sadka (NYSE); Tripathi et al. (2021) 30m NSE imbalance decay |
| **3** | **`LATE_MOMENTUM` (S3)** | MIS Intraday (Both sides) | Trend / High-Vol ($ADX_{5d} \ge P_{40}$) | 13:45–14:30 swing extreme (0.5%–1.0%) | $p^* \approx 62.9\%$ (q=0.0) | Gao, Han, Li & Zhou (2018); SPY/NIFTY late-session institutional rebalancing |
| **4** | **`CAS_REVERSAL` (S4)** | CNC Delivery (Long-only) | Closing Imbalance / Event | Next morning pre-open $< P_{CAS} - 1.0\%$ | Reversion $> 0.40\%$ | Bogousslavsky & Muravyev (2023); NSE CAS operational from 3 Aug 2026 |

---

## 1. Premise Checks P1–P6: Analytical Verification

### P1. Statutory & Broker Fee Schedule (Dhan / NSE Cash Segment, September 2026)

The desk's prior assumption of a flat "0.15% round-trip friction" overstates MIS costs by 41% and understates CNC delivery costs by 40%. The exact verified schedule on ₹58,333 notional is itemized below:

```
+---------------------------------------------------------------------------------------------------+
| Statutory / Broker Component    | MIS Intraday (Turnover ₹1.167L) | CNC Delivery (Turnover ₹1.167L)   |
+---------------------------------+---------------------------------+-----------------------------------+
| Brokerage (min Rs 20, 0.03%)    | ₹34.98 (₹17.49 x 2)             | ₹0.00 (Zero brokerage)            |
| STT (MIS: 0.025% sell; CNC: 0.1%)| ₹14.58 (Sell only)              | ₹116.67 (Buy + Sell @ 0.10%)       |
| NSE Exchange Txn (0.002970%)    | ₹3.46                           | ₹3.46                             |
| SEBI Turnover (Rs 10 / Cr)      | ₹0.12                           | ₹0.12                             |
| Stamp Duty (Buy: 0.003% / 0.015%)| ₹1.75                           | ₹8.75                             |
| GST (18% on Brok + Exch + SEBI) | ₹6.94                           | ₹0.65                             |
| DP Charge (Dhan: Rs 12.50 + GST)| ₹0.00                           | ₹14.75 (Sell side only)           |
+---------------------------------+---------------------------------+-----------------------------------+
| Total Round-Trip Friction       | ₹61.86 - ₹61.99 (0.106%)        | ₹144.40 - ₹144.52 (0.248%)        |
+---------------------------------+---------------------------------+-----------------------------------+
```

#### Friction Scaled by Stop Width ($c = \frac{\text{Friction}}{1R}$)
Because the ₹58,333 slot cap binds whenever stop width $S \le 2.5714\%$, the actual risk per share scales linearly with stop percentage:
$$1R = \min(₹1,500, ₹58,333.33 \times S)$$

$$\begin{array}{rcccc}
\hline
\text{Stop Width } (S) & 1R \text{ Rupees} & \text{MIS Friction (₹61.86)} & \text{CNC Friction (₹144.40)} & \text{Old Assumed 0.15\% (₹87.50)} \\
\hline
0.50\% & ₹291.67 & \mathbf{0.212R} & \mathbf{0.495R} & 0.300R \\
1.00\% & ₹583.33 & \mathbf{0.106R} & \mathbf{0.248R} & 0.150R \\
1.50\% & ₹875.00 & \mathbf{0.071R} & \mathbf{0.165R} & 0.100R \\
2.57\% & ₹1,500.00 & \mathbf{0.041R} & \mathbf{0.096R} & 0.058R \\
\hline
\end{array}$$

*Critical Implication:* At a 0.50% stop, CNC friction consumes **half of the initial risk budget (0.50R)** before the trade has even moved.

---

### P2. Breakeven Win Rate Hurdle Formulation

For any trade governed by the Two-Tranche Bracket Model:
- Tranche 1 (50%) targets $+1.5R$.
- Tranche 2 (50%) targets $+3.0R$ with stop trailed to Breakeven ($0.0R$).
- Let $p$ be the probability of reaching Tranche 1 before stopping out at $-1.0R$.
- Let $q$ be the conditional probability that the runner reaches $+3.0R$ before hitting the trailed breakeven stop, given that Tranche 1 filled.
- Let $c$ be the total round-trip friction in R-multiples.

The expected net return $\mathbb{E}[\text{Net } R]$ is:
$$\mathbb{E}[\text{Net } R] = p \left(0.5 \times 1.5 + 0.5 \times 3.0q\right) - (1 - p)(1.0) - c = 0$$
$$p(0.75 + 1.5q + 1.0) - 1.0 - c = 0$$
$$\mathbf{p^* = \frac{1 + c}{1.75 + 1.5q}}$$

#### Hurdle Calibration Table ($p^*$)

$$\begin{array}{ccccc}
\hline
\text{Friction } c & q = 0.0 \text{ (Runner Never Hits T2)} & q = 0.50 \text{ (Random Walk Runner)} & q = 1.0 \text{ (Runner Always Hits T2)} \\
\hline
0.10R & 62.86\% & \mathbf{44.00\%} & 33.85\% \\
0.15R & 65.71\% & \mathbf{46.00\%} & 35.38\% \\
0.25R & 71.43\% & \mathbf{50.00\%} & 38.46\% \\
0.40R & 80.00\% & \mathbf{56.00\%} & 43.08\% \\
0.50R & 85.71\% & \mathbf{60.00\%} & 46.15\% \\
\hline
\end{array}$$

*Random Walk Benchmark:* Under pure driftless diffusion, the probability of hitting $+1.5R$ before $-1.0R$ is exactly $\frac{1}{1 + 1.5} = 40.0\%$, and $q = 50.0\%$. Gross expectancy is zero. A strategy creates positive expectancy if and only if it raises $p > 40.0\%$ or $q > 50.0\%$ by an amount sufficient to overcome friction $c$.

---

### P3. Stop Width & Noise Realism: The Diffusion Hazard

Let daily volatility for liquid mid-cap F&O underlyings be $\sigma_d \approx 2.50\%$. Continuous trading spans 24 15-minute bars (continuous trading ends at 15:15 IST before CAS):
$$\sigma_{15m} \approx \frac{2.50\%}{\sqrt{24}} \approx 0.510\%$$
$$\mathbb{E}[\text{High} - \text{Low}]_{15m} \approx 1.6 \times \sigma_{15m} \approx 0.816\%$$

1. **Noise Enclosure:** A 0.50% stop ($S = 0.005$) is smaller than the typical range of a single 15-minute bar ($0.82\%$).
2. **First-Passage Probability within One Session:**
   $$P(\text{Touched within } 24 \text{ bars}) \approx 2 \cdot \Phi\left(-\frac{S}{\sigma_d}\right)$$
   - At $S = 0.5\%$: $P(\text{Stop Hit by Noise}) \approx 84.1\%$.
   - At $S = 1.0\%$: $P(\text{Stop Hit by Noise}) \approx 68.9\%$.
   - At $S = 1.5\%$: $P(\text{Stop Hit by Noise}) \approx 54.9\%$.
3. **Resolution Horizon:** Under Brownian motion, expected time to exit a $[-1R, +1.5R]$ corridor is:
   $$\mathbb{E}[T] = \frac{a \cdot b}{\sigma^2} = \frac{1.0 \cdot 1.5 \cdot S^2}{\sigma_{15m}^2}$$
   - At $S = 0.5\%$: $\approx 1.44 \text{ bars (21.6 minutes)}$.
   - At $S = 1.0\%$: $\approx 5.76 \text{ bars (86.4 minutes)}$.
   - At $S = 1.5\%$: $\approx 13.0 \text{ bars (195.0 minutes)}$.

*Audit Diagnosis:* Any backtest replay showing zero same-day resolutions across multiple signals either possesses broken intrabar resolution logic or is executing with daily-ATR stops far wider than 1.5%.

---

### P4. Disqualification of Explored Directive Angles

1. **Order-Flow Imbalance (OFI) as a Standalone Alpha**:
   - Tripathi, Dixit & Vipul (2021, *Finance Research Letters*, 195 active NSE stocks) proved that order imbalance predictability on NSE dies within **30 minutes**.
   - With 250–500 ms retail execution latency, 1-second WebSocket snapshots, and 0.11%–0.25% round-trip friction, the alpha decays before an entry and exit can clear.
   - **Verdict:** Demoted strictly to an **intraday entry-confirmation filter** ($OFI_{60s} \ge +0.15$).
2. **Dynamic Price Band Flex "Arbitrage" (NSE/FAOP/62241 & 63405)**:
   - Dynamic price bands flex in steps of 5%, 5%, 3%, 3%, 2% upon touching 9.90% of base price.
   - However, flexing requires meeting strict UCC (Unique Client Code) and TM (Trading Member) participation counts which **cannot be observed from a retail 5-level depth feed**.
   - Furthermore, per NSE/FAOP/64995, resting orders beyond band limits are automatically purged by the exchange engine.
   - **Verdict:** Demoted strictly to a **Fail-Closed Kill Switch** (freeze all entries within 2% of the dynamic band limit).
3. **Futures-Spot Lead-Lag**:
   - Empirical studies on NIFTY index and stock futures (IGIDR 2007; Khan et al. 2022) prove the information lag is strictly **less than 1 to 2 minutes**.
   - At 15-minute bar decision cadence, the cross-asset lead-lag has completely dissipated.
   - **Verdict:** Disqualified.
4. **Volume Profile / Point of Control (POC) Migration**:
   - Zero peer-reviewed or statistically validated evidence exists for POC migration edge on NSE cash equities.
   - **Verdict:** Disqualified.

---

## 2. Formal Specification of the 4 Recommended Strategies

### S1. Results-Day Drift Sleeve (`PEAD_DRIFT`, Long-Only CNC)
- **Economic Mechanism:** Structural post-earnings announcement under-reaction due to institutional capital deployment inertia and analyst forecast revisions.
- **Universe:** Active F&O constituents reporting quarterly financial results.
- **Entry Conditions:**
  1. Standardized Unexpected Earnings: $\text{SUE} = \frac{\text{EPS}_q - \text{EPS}_{q-4}}{\sigma(\Delta \text{EPS}_{8q})} \in \text{Top Decile}$ of quarter-to-date reports.
  2. Day-0 return from previous close $> 0$ AND exceeds its sector index Day-0 return.
  3. Enter on the first 15-minute bar close on Day +1 that holds strictly above Day-0 VWAP.
- **Structural Invalidation Stop:** Exit if price closes a daily session below the Day-0 low (typically 3.0% to 6.0% wide).
- **Position Sizing:** Because $S > 2.57\%$, size down notional so that absolute risk is clamped to ₹1,500:
  $$\text{Shares} = \left\lfloor \frac{₹1,500}{\text{Entry} - \text{Day-0 Low}} \right\rfloor$$
- **Exits:** Tranche 1 (+1.5R), Tranche 2 trails the 10-day rolling low. Hard time exit at Day +40.
- **Kill Switches:** India VIX $+20\%$ intraday shock; scrip in F&O ban or ASM; promoter block sale within 5 days post-results; rolling 30-trade net $R < 0$.

---

### S2. Liquidity-Shock Reversal (`SWEEP_RECLAIM`, MIS Both Sides)
- **Economic Mechanism:** Absorption of temporary order-book imbalances caused by stop cascades through prominent session levels (Heston, Korajczyk & Sadka 2010).
- **Regime Gate:** Range or chop environment: 5-day $ADX < P_{40}$ AND NIFTY intraday range $< Median_{60d}$.
- **Reference Level:** $L = \min(\text{Prior Day Low}, \text{Opening Range Low})$ (15m bars).
- **Trigger Conditions (Long; Short is Symmetric):**
  1. $Low_t < L - 0.15 \times ATR_{15}$ (liquidity sweep).
  2. $Close_t > L$ (intrabar reclaim) OR next bar close $> L$.
  3. $Volume_t > 2.0 \times \text{Median Volume}_{slot, 20d}$.
  4. Close Location Value $\frac{Close - Low}{High - Low} \ge 0.60$.
  5. 5-Level Depth Filter: Mean order flow imbalance $\frac{\Sigma Bid - \Sigma Ask}{\Sigma Bid + \Sigma Ask} \ge +0.15$ over 60 seconds post-bar close.
- **Invalidation Stop:** Sweep Low $- 1 \text{ tick}$. Stop distance must lie within $0.5\% \le S \le 1.5\%$; skip trade if $S > 1.5\%$.
- **Exits:** Tranche 1 at $+1.5R$. Tranche 2 at $+3.0R$ or opposite side of session range. Mandatory hard flat at 15:10 IST.
- **Kill Switches:** Earnings announcement day; price within 2% of dynamic circuit band; NIFTY move $> 1.2\%$ in sweep direction; monthly Tuesday F&O expiry session.

---

### S3. Late-Session Market-Momentum Continuation (`LATE_MOMENTUM`, MIS Both Sides)
- **Economic Mechanism:** Institutional end-of-day rebalancing, gamma hedging, and passive benchmark replication flow (Gao, Han, Li & Zhou 2018).
- **Regime Gate:** Trend / high-volatility days ($ADX_{5d} \ge P_{40}$ OR NIFTY range $\ge Median_{60d}$).
- **Market Alignment Trigger:**
  - $r_1$: NIFTY return from previous official close to 09:45 IST.
  - $r_{mid}$: NIFTY return from 09:45 to 14:15 IST.
  - Condition: $\text{sign}(r_1) == \text{sign}(r_{mid})$ AND $|r_1| > 0.5 \times \sigma_{60d}(r_1)$.
- **Stock Selection:** Top 3 F&O stocks with highest 60-day beta to NIFTY in the signal's direction whose 14:15 bar closes beyond session VWAP.
- **Entry & Stop:** Enter on 14:15–14:30 bar close. Stop placed beyond the 13:45–14:30 swing extreme ($0.5\% \le S \le 1.0\%$).
- **Exit & Hurdle:** Because continuous trading terminates at 15:15, only 3 bars remain. Runner probability is calibrated to $q \approx 0.0$. Tranche 1 at $+1.5R$, all remaining shares flat at 15:10 IST. Hurdle is strictly $p^* \approx 62.9\%$ (evaluated at $q = 0$).
- **Kill Switches:** $|r_1| > 2\sigma$ opening gap; CAS reference price dislocation; India VIX $> P_{90}$.

---

### S4. CAS Dislocation Overnight Reversal (`CAS_REVERSAL`, Long-Only CNC)
- **Economic Mechanism:** Structural liquidity provision during the newly instituted NSE Closing Auction Session (effective 3 August 2026). Passive rebalancing imbalances cause temporary auction dislocations that mean-revert by next morning's open (Bogousslavsky & Muravyev 2023).
- **Regime:** Index rebalance dates, month-end settlement, or large closing sell imbalances.
- **Entry Trigger:**
  - Dislocation $D = \frac{P_{indicative, 15:27} - LTP_{15:15}}{LTP_{15:15}} \le -0.60\%$.
  - Disseminated CAS auction imbalance is on the sell side.
  - Submit CAS limit buy order during Order Entry Session II (15:25–15:30 IST) at the indicative price.
- **Invalidation Stop:** Pre-open morning check: exit immediately at 09:15 open if open prints below $P_{CAS} - 1.0\%$.
- **Exits:** Tranche 1 at 15:15 reference LTP (or 09:30 open). Tranche 2 closed by 10:15 IST. Capital is recycled into intraday slots.
- **Cost Hurdle:** CNC round trip is 0.25%. Expected dislocation must exceed $> 0.40\%$ to achieve statistical edge.
- **Kill Switches:** Stock-specific corporate news post-15:15; dislocation caused by $\pm 3\%$ CAS band clamp; broad market crash (NIFTY down $> 1.5\%$).

---

## 3. Pre-Registered Evaluation Protocol & Promotion Milestones

To ensure zero p-hacking and prevent overfitting across the incubation slate:

1. **Pre-Registration Invariant:** Model parameters, entry triggers, and exit ladders are permanently frozen in Git before historical data is loaded.
2. **Purged Walk-Forward Partitioning:**
   - 24-month rolling in-sample training window.
   - 6-month out-of-sample testing window with a 5-day embargo.
   - Out-of-Sample Holdout: August 2025 – July 2026 permanently sequestered.
   - Post-CAS Data (effective 3 August 2026) evaluated as an independent structural regime.
3. **Statistical Power & Sample Size Gate:**
   - For an expected edge of $+0.15R$ with $\sigma_R \approx 1.30R$:
     $$N_{t \ge 2.0} \approx \left(\frac{2.0 \times 1.30}{0.15}\right)^2 \approx 300 \text{ trades}$$
     $$N_{\text{Power } 80\%} \approx 590 \text{ trades}$$
   - The desk rejects any statistical claim derived from fewer than 300 trades.
4. **Multiple-Testing Deflation (DSR & SPA):**
   - For $K$ evaluated strategy variants, the Deflated Sharpe Ratio (Bailey & López de Prado 2014) and Hansen's Superior Predictive Ability (SPA 2005) must satisfy:
     $$\text{DSR} \ge 0.95 \quad \text{and} \quad p_{SPA} \le 0.05$$
5. **Staged Promotion Milestones:**
   - **Phase 1 (Shadow Observation):** 100 prospective live sessions logged in `CHATGPT/observation_log.csv` with zero capital. Verify empirical $p$, $q$, and slippage against $p^*$.
   - **Phase 2 (Micro-Scale Testing):** Capital deployed at ₹500 risk budget after passing Phase 1.
   - **Phase 3 (Full Capital Deployment):** Scale to full ₹1,500 risk budget only after 300 live trades with verified positive net expectancy ($E > 0$).
