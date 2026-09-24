# BEACON mandate: red-team audit of Track 2 (Rule 8)

Author: Claude (red team) | 24 September 2026 | Track 2 only | Paper only (Rule 1)

**Scope.** This report responds to `shared/track2_liquid/track2_master_engineered_audit_mandate.md`, as scoped by the relayed request:
- fact-check three code claims;
- review Section 1 (bar-close adverse selection and the 4-state queue model);
- review Section 2 (the seven strategies);
- review Section 3 (GEX sign, OI walls, Max Pain, futures basis).

No production file was changed. Everything below was measured at `HEAD 93cebcb` in the shared workspace.

**Evidence.** Every number comes from one of two read-only scripts. You can rerun them:
- `Claude outputs/2026-09-24_beacon_probes/probe_code_claims.py` calls the production classes with synthetic inputs.
- `Claude outputs/2026-09-24_beacon_probes/probe_empirical.py` runs the production strategy code bar by bar over the repo's only history file, `shared/track2_liquid/historical_candles_track2.json`.

**Relation to Codex.** Codex has written `shared/reviews/beacon_institutional_audit_20260924.md` (untracked). Where we agree, this report says so and does not repeat Codex's derivations. Where this report adds or disagrees, it gives the evidence.

**Limits of the empirical work:**
- **Small sample.** It covers 8 hand-picked high-beta stocks over 32 sessions (10 Aug to 23 Sep 2026), which is one market regime. It uses 15-minute bars only.
- **Optimistic entries.** Entry is assumed at the signal-bar close, an E1 assumption that flatters every strategy.
- **Trend gates disabled.** The daily trend gates were off, because 32 daily bars cannot support an EMA50.
- **Confidence intervals too narrow.** The bootstrap treats stock-days as independent. Same-bar correlation is 0.32, so the true intervals are wider than shown.

These numbers are **evidence about mechanics and plausibility, not an estimate of edge.** No number here should be tuned on.

---

## 0. Verdict

1. **The seven-strategy ensemble cannot run.** `MultiStrategyEngine.evaluate_symbol()` raises `AttributeError` on any session with 2 or more bars. COMPASS is the smaller problem.
2. **Not one of the seven strategies shows positive expectancy in the repo's own data.**
   - VWAP_RECLAIM and VOL_SQUEEZE are negative at every threshold tested, and their 95% intervals exclude zero.
   - The rest are indistinguishable from zero.
   - Adding derivatives overlays to this set adds parameters without adding evidence.
3. **The mandate's economics are wrong on three counts:**
   - Its expectancy table assumes every winner runs to +3.0R.
   - It assumes ₹1,500 of risk per trade, whereas the ₹58,333 cap cuts the median to ₹832.
   - It double-counts friction on winners.
   - What was measured: only 10% of ORB breaks reach T1, and 73% end at the 15:00 time exit.
   - At the mandate's own 44% win rate, ORB's measured expectancy is about 0R, not +22%.
4. **The friction "systemic bug" is real arithmetic but mostly harmless.**
   - In 3 of the 4 files the cost filter can never trigger, because each strategy's minimum-risk floor rejects those trades first.
   - It changes decisions only in RECOIL.
   - The real cost problem is different: the brackets default to CNC, which costs 0.222% round trip against the 0.106% the strategies assume.
5. **The CAS "race condition" is misdiagnosed.**
   - The code at `track2_paper_execution.py:570` has no production caller.
   - It defaults to the 15:25 non-CAS cutoff and to CNC, which has no cutoff at all.
   - The 15:10 hard-flat is enforced nowhere.
   - Dhan's own cash intraday auto square-off is 15:10 (checked on dhan.co today). If Dhan is the broker, the desk's "safety deadline" is the broker's own square-off time, with zero margin.
6. **Passive entries on breakouts are adversely selected, measurably.**
   - A resting bid 0.25 × bar-range below the breakout close fills on 81% of losing trades but only 39% of winning trades.
   - The win share among fills falls from 43% to 26%.
7. **The paper gate cannot detect the edge being claimed.** Outcome dispersion is σ_R = 0.74.
   - At 20 fills, the 95% interval is ±0.32R.
   - Detecting +0.10R needs about 340 trades.

**Consequence:** stop adding strategies. Fix the engine, the session policy and the evidence symmetry. Then run one strategy forward with a counterfactual ledger, sized by a power calculation rather than by "60 sessions / 20 fills".

---

## 1. Fact-check of the three code claims (and what they missed)

### 1.1 Friction double counting in four strategies. Confirmed, but inert in three of four.

| File:line | Code | Double-counted? | Filter can reject when | Strategy's minimum-risk floor | Effect |
|---|---|---|---|---|---|
| `track2_trapdoor_strategy.py:230` | `(notional + shares×target) × 0.00106` | Yes (both legs × a round-trip rate) | risk < 0.354% of price | 0.40% | **Never triggers** |
| `track2_compass_strategy.py:263` | same | Yes | risk < 0.319% | 0.40% | **Never triggers** |
| `track2_last_light_strategy.py:271` | `notional × 0.00106 × 2.0` | Yes | risk < 0.424% | 0.50% | **Never triggers** |
| `track2_recoil_strategy.py:280` | `notional × 0.00106 × 2.0` | Yes | VWAP target distance < 0.636% (correct: 0.318%) | none on target distance | **Changes decisions**: 3 of the 5 cost rejections in the sample would pass with single counting |

The thresholds come from $3\,C \le G$:
- Gain is $G = k\,r\,q$ (target multiple × risk per share × shares).
- Coded cost is $C = f\,(2E + k r)\,q$ for TRAPDOOR and COMPASS, and $C = 2fEq$ for the other two.
- With $f = 0.00106$, the filter rejects when $r/E < 6f/(k(1-3f))$ (TRAPDOOR and COMPASS) or $r/E < 4f$ (LAST_LIGHT at T1 = 1.5R). See probe P1b.

**The rate is right. The product it's applied to is wrong:**
- `calculate_transaction_costs` gives 0.1060% round trip for MIS at slot size.
- For CNC it gives **0.2222%** (probe P1).
- `BracketOrderManager.create_bracket` defaults to `product_type="CNC"` (`track2_paper_execution.py:488`).

Any paper trade opened through that default is charged delivery costs, while every strategy filters on MIS costs. Remove the four constants and use the one fee function, with a product type that must be passed explicitly.

**The mandate commits the same error in its own table:**
- The +₹3,251 win equals ₹3,375 − 2 × ₹61.8, so friction is counted twice on wins.
- The −₹1,562 loss equals ₹1,500 + ₹61.8, so it is counted once on losses.
- It then calls the same ×2 a bug in LAST_LIGHT and RECOIL.

### 1.2 "CAS 15:10 vs broker RMS 15:12 race condition at line 570". Misdiagnosed.

What the code actually does (probe P3 and grep):

1. **Nothing calls it.** `BracketOrderManager.create_bracket` and `update_bracket_quote` have no callers outside `tests/`. The cutoff at line 570 is not in any path that runs.
2. **The CAS flag is off by default.** `is_cas_eligible` defaults to `False`, which gives a 15:25 cutoff, and no caller anywhere sets it. After CAS, every Track 2 stock is a CAS stock, so the default is wrong for 100% of the universe. (Probe: MIS at 15:13 with the default flag gives `None`, meaning still open.)
3. **The product default has no cutoff.** `product_type` defaults to `"CNC"`, which skips the cutoff entirely.
4. **No clock, no cutoff.** If `current_time_ist` isn't passed, the cutoff is never checked. (Probe: MIS at 15:40 with no clock gives `None`.)
5. **15:10 is written down but never enforced.** It appears only as a docstring, as the string field `hard_flat_time="15:10:00"` on `LastLightSignal`, and as a display label in `track2_live_radar.py:589`.
6. **The broker's RMS time depends on the broker.**
   - Dhan's support page (fetched 24 Sep 2026) says: from the day CAS applies, equity cash intraday auto square-off is **15:10**, and derivatives are 15:25.
   - Codex cites Zerodha at 15:12 with a ₹50 + GST charge.
   - The mandate's statement "Zerodha/Dhan RMS 15:12" is wrong for Dhan.
   - If Dhan is the broker, a desk "hard-flat" at 15:10 is not a safety margin; it *is* Dhan's square-off.
   - The exit ladder must end before the broker's time, not on it: last new entry 14:50, cancel entries 15:00, passive exits 15:03, aggressive exits 15:06, flat and reconciled by 15:08. That is the same shape as Codex's §1.4, with Dhan's clock.
7. **The CAS schedule in the mandate (15:15 to 15:30, matching 15:23 to 15:28) is wrong.** Codex cites NSE for **15:15 to 15:35**. My fetch of the NSE page timed out, so I have not independently confirmed that source today. My 23-Sep audit recorded the same 15:15 to 15:35 window.

**Evidence asymmetry, which matters more than the clock:**

| Event | Requires `execution_evidence`? | Probe result |
|---|---|---|
| T1 or T2 target | **Yes** | Price touched 108 with T1 at 107.5, no evidence: T1 **not** filled |
| Stop | **No** | One quote at 94: `STOPPED_OUT_FULL`, net −₹510.28 |
| Time square-off | **No** | 15:13 with the CAS flag: `CLOSED_MIS_SQUAREOFF`, net +₹89.34 at LTP |

A ledger that needs proof for good outcomes but books bad outcomes, and deadline exits, on one quote is not conservative. It is inconsistent, and its bias depends on the path. **Every exit, of any kind, must be either evidenced or tagged `MODELLED_NOT_EVIDENCED`**, and modelled exits must be excluded from qualification. This matches Codex's P0 findings on the same method.

### 1.3 "COMPASS evaluation gap". Confirmed, but it understates the problem: the whole engine cannot run.

Probe P2 fed `MultiStrategyEngine().evaluate_symbol(...)` 1, 2, 6 and 8 real-shaped bars:
- **1 bar:** 0 signals.
- **2, 6 and 8 bars:** `AttributeError: 'MultiTimeframeAlphaEngine' object has no attribute 'evaluate_candidate'`.

The call sites that break are:
- `track2_multi_strategy_engine.py:123` calls `self.orb_engine.evaluate_candidate(...)`. That method doesn't exist; the class has `evaluate_15m_orb`.
- `track2_multi_strategy_engine.py:257` calls `self.trapdoor_engine.evaluate_setup(...)`. That method doesn't exist either; TRAPDOOR defines `evaluate`. Even with the ORB call fixed, the engine would crash from 6 bars onward, which on a live day is 10:45.
- `compass_engine` is built at line 85 and never referenced in `evaluate_symbol`.

Why 827 green tests didn't catch this:
- `tests/test_track2_multi_strategy_engine.py` exercises `rank_and_allocate` with hand-built `UnifiedTradeSignal` objects.
- `tests/test_track2_last_light_recoil.py` calls the strategies directly.
- No test calls `evaluate_symbol` with a real session.
- No daemon imports the engine (grep of `antigravity/daemons`).

The "815 passing tests" in the mandate's preamble (827 after today's merges) prove the code does what the tests ask. They say nothing about whether the ensemble works.

### 1.4 Other code facts that contradict the mandate's premises

| Mandate premise | Code fact | Evidence |
|---|---|---|
| "Strict ₹58,333 slot ceiling" | The governor checks only the ₹1,75,000 total. A single ₹1,50,000 position (2.6 slots) is **approved**. VWAP_RECLAIM and VOL_SQUEEZE cap notional at ₹2,00,000. The screener's ORB caps at ₹1,00,000. | Probe P4; `track2_vwap_reclaim_strategy.py:97`; `track2_volatility_squeeze_strategy.py:92`; `liquid_momentum_screener.py:348` |
| RECOIL trades short fades | The governor rejects any stop above entry (`INVERTED_STOP`), so every RECOIL SELL dies at the risk gate. `UnifiedTradeSignal` also has no side field (Codex P0). | Probe P4 |
| Time stops (TRAPDOOR 4 bars, COMPASS 6, RECOIL 4) and TRAPDOOR's single 1.8R target | The engine overwrites TRAPDOOR's T2 with 3.0R. `max_holding_bars` is only data; nothing enforces it. | `track2_multi_strategy_engine.py:280-281`; grep |
| Conviction ranks signal quality | TRAPDOOR, LAST_LIGHT and RECOIL get a hard-coded **maximum** volume score (`0.30 × 1.0`, `volume_multiple=1.5/2.2`) whatever the real volume. VWAP_RECLAIM gets the highest base weight (0.315). The ranking therefore favours the fixed-score strategies and the worst performer in the data (§3). | `track2_multi_strategy_engine.py:266-368` |
| LAST_LIGHT runs only 14:00 to 14:30 | If the bar timestamp has no `T` (e.g. `"2026-09-24 11:00:00"`), parsing fails silently and the time window is **skipped**. | Probe P5: `TIME_WINDOW_BLOCKED` becomes `NO_PATTERN` (window not applied) |
| RVOL uses a 20-session bucketed median | `SharedFeatureEngine.calculate_rvol` floors the baseline at 1,000 shares. With a missing baseline, 50,000 shares gives **RVOL 50**, so every volume gate passes. VWAP and SQUEEZE fail closed on the same input; the shared-feature strategies fail open. | Probe P6 |
| ATR20 is a stable volatility unit | `calculate_atr20` uses today's bars only, includes the signal bar, and averages across the U-shaped day. At 10:45, 12:00 and 14:00 it is **1.75×, 1.63× and 1.63×** the typical range of the bar being judged. Every "×ATR20" threshold therefore means something different at each time of day. | probe_empirical §A–B |
| Aggregate open risk ₹4,500 | True only through `calibrate_for_corpus()`. A bare `PortfolioRiskGovernor()` uses ₹6,000 aggregate, ₹1,00,000 capital and the VAR+ELM gate **off**. The three daemons that build a governor all use `calibrate_for_corpus`; any new caller that doesn't inherits the looser defaults. | Probe P4; grep |
| The history file can drive the strategies | 32 daily bars cannot compute the EMA50 that ORB, VWAP and SQUEEZE require. A 20-session volume baseline leaves 12 usable sessions. | Data inspection |

---

## 2. Section 1: microstructure adverse selection and the 4-state fill model

### 2.1 What can and cannot be measured with the repo's data

The only history is 15-minute OHLCV. That can measure:
- the bar-to-bar path after a signal closes;
- whether a resting price was touched or traded through.

It cannot measure:
- the spread;
- queue position;
- trade aggressor side;
- the seconds right after a bar boundary, where crowding happens.

**E3 evidence cannot be produced from bars.** The only defensible bar-level rule is:

$$
\text{fill}_{\text{buy limit }p} =
\begin{cases}
\text{certain} & \text{if } \min(\text{low}) \le p - \text{tick} \quad (\text{traded through}) \\
\text{unknown} & \text{if } \min(\text{low}) = p \quad (\text{touched}) \\
0 & \text{if } \min(\text{low}) > p
\end{cases}
$$

An "unknown" result can never count toward qualification.

### 2.2 Implementation shortfall for a marketable entry at bar close

Definitions:
- Decision price $P_d$ is the signal-bar close.
- Order arrival is at $t_0+\Delta$.
- Side is $s=+1$ for a buy.
- Fills $(p_j,q_j)$, total $Q=\sum q_j$.

$$
IS_{bps} = 10^4\,\frac{s\,(\bar p - P_d)}{P_d}
= \underbrace{\tfrac12 S_{bps}(\tau)}_{\text{spread}}
+ \underbrace{10^4\,\frac{s\,(m_{t_0+\Delta}-m^\ast_{t_0})}{P_d}}_{\text{delay / crowding}}
+ \underbrace{10^4\,\frac{s\,(m^\ast_{t_0} - P_d)}{P_d}}_{\text{close-print bias}}
+ \underbrace{BW(Q)}_{\text{book walk}}
$$

Where:
- $m$ is the mid-price.
- $m^\ast_{t_0}$ is the mid at the boundary.
- The close-print bias exists because the last trade of a breakout bar is more often an ask-side print, so the close already contains part of the spread.
- $BW(Q) = 10^4\,\sum_\ell (a_\ell - a_1)\,\min(q_\ell, Q_{\text{rem}})/(Q\,P_d)$ is the cost of walking beyond the best ask.

**Magnitudes at Track 2 size (derived, not measured):**
- **Spread.** Half a tick costs 0.8 bps at ₹300 with a ₹0.05 tick, 1 bp at ₹50 with a ₹0.01 tick, and 0.5 bps at ₹1,000 with a ₹0.10 tick. One to two ticks of quoted spread therefore costs 1 to 4 bps.
- **Square-root impact.** $I = Y\,\sigma_d\sqrt{Q/V_d}$ with Q = ₹58,333, V_d = ₹30 Cr and σ_d ≈ 3% gives √(Q/V) = 0.0139 and **I ≈ 2 to 3 bps**. At V_d = ₹300 Cr it is 0.7 to 1 bp. I agree with Codex that the square-root law describes metaorders, not a single child order. For one marketable order no larger than the best-ask quantity, the correct impact is $BW(Q)=0$. Almgren–Chriss is the wrong tool at this size.
- **Delay noise.** A midday 15-minute range of 0.25% gives σ₁₅ ≈ range/1.665 ≈ 0.15%. Over a 2-second latency that is ≈ 0.15% × √(2/900) ≈ **0.7 bps**, rising to about 1.8 bps in the 09:30 bar. Latency noise is small.
- **Crowding drift at the boundary.** $E[m_{t_0+\Delta}-m^\ast_{t_0}]$ is **unknown** and is the only term that could be large. It can only be estimated from E3 tick data.

**Proposed slippage function.** It has the shape the mandate asked for, but **every coefficient must be estimated, not assumed**:

$$
\widehat{IS}_{bps}(\tau,\sigma_{15},S) = a_0 + a_1\,\tfrac12 S_{bps} + a_2\,\sigma_{15,bps}\sqrt{\Delta/900} + a_3\,\mathbb 1[\tau\in\text{boundary}\pm 5s] + a_4\,\mathbb 1[\tau\in 09{:}30\text{–}09{:}45] + a_5\,BW(Q)
$$

Fit it by OLS on logged E3 entries, clustered by day. Until at least 100 entries are logged, use a stress value of **2 ticks + measured half-spread** per side in every evaluation, and keep the fitted IS alongside it.

### 2.3 What the data show: close-at-extreme selection and adverse selection (probe_empirical §D2)

**Sample:** 103 first closes above the 09:15 bar's high, one per stock-day at most, across all 32 sessions and 8 stocks. The bracket is fixed: stop at the OR low, 1.5R/3.0R targets, flat at 15:00. A resting bid is placed at close − x × (signal-bar range) and left for one bar.

| x | P(fill) | Mean R, filled | Mean R, unfilled | Gap (95% CI) | P(fill \| winner) | P(fill \| loser) | Win share among fills |
|---|---|---|---|---|---|---|---|
| 0.10 | 86% | −0.14 | +0.30 | −0.45 (−0.92…+0.03) | 80% | 92% | 39% |
| 0.25 | 63% | −0.35 | +0.37 | **−0.72 (−1.00…−0.45)** | **39%** | **81%** | **26%** |
| 0.50 | 30% | −0.42 | +0.06 | −0.48 (−0.75…−0.20) | 16% | 41% | 23% |

All signals together have a 42.7% win share.

**Model.** Let $p$ be the unconditional win probability and $\phi_W, \phi_L$ the fill probabilities given a winner or a loser. The win rate among filled trades is:

$$
p_f=\frac{p\,\phi_W}{p\,\phi_W+(1-p)\,\phi_L}
$$

At x = 0.25: 0.427 × 0.39 / (0.427 × 0.39 + 0.573 × 0.81) = **0.264**, which matches the measured 26%.

A passive bid below a breakout close is not "better price". It is a filter that selects the breakouts that fail. The 15-minute data cannot separate the mechanical part (a bar that dips is closer to the stop) from the informational part. Both hurt the strategy in the same direction.

**Implication for the entry order:**
- Use a marketable limit on breakouts. That has its own selection bias with the opposite sign: when the clamp aborts, it is disproportionately skipping winners.
- **Therefore every aborted or unfilled signal must be written to a counterfactual ledger**, with the same bracket tracked from the decision price. The strategy is judged on fills *and* misses:

$$
E[\text{PnL per signal}] = \phi_W\,p\,\bar W - \phi_L\,(1-p)\,\bar L
$$

**Clamp specification.** This replaces the mandate's $P_{entry}+\delta\,ATR_{15m}$ term, which uses the time-of-day-biased session ATR.

$$
P_{lim}=\operatorname{floor}_{tick(P)}\!\Big(\min\big(A_{t_0+\Delta}+k\cdot tick,\;P_d\,(1+b_{\max})\big)\Big),\quad k\in\{1,2\},\; b_{\max}=\max(2\,tick/P_d,\ 5\text{ bps})
$$

Rules:
- If $A_{t_0+\Delta} > P_{lim}$, don't submit: record `MISSED_CLAMP`.
- Submit as IOC. Size the bracket to the **filled** quantity.
- Tick size must come from the current NSE price-band table, not the hard-coded 0.05 in `track2_paper_execution.py` (my 23-Sep finding A26).

### 2.4 The 4-state queue model, corrected

**Cancellation haircut.** I agree with Codex (§1.2, line 71): $V_{cum}\ge R_0/\eta+Q$ contradicts its own definition of η. Cancellations ahead of you *shorten* the queue. Pick one interpretation and use it consistently:
- If η is a **cancellation fraction**, the scenario queue is $(1-\eta)R_0$.
- If η is the **usable fraction of bar volume**, the condition is $\eta V\ge R_0+Q$.

**Event-driven model.** It is side-aware, and I'm adding it because Rule 4's text conflates the sides.

For a buy limit at price $p$, acknowledged at $t_0$, with displayed quantity $L_0$ at $p$ and so queue ahead $R_0=L_0$:

$$
R(t)=\max\Big(0,\;R_0-\!\!\sum_{j:\,t_0<t_j\le t,\;\pi_j=p}\!\! v_j-\hat C_{\text{ahead}}(t)\Big),\qquad
f(t)=\min\Big(Q,\;\max\big(0,\textstyle\sum v_j-(R_0-\hat C_{\text{ahead}}(t))\big)\Big)
$$

Here $v_j$ is the size of a sell-initiated trade at $p$.

**Trade-through rule:** any trade at a price below $p$ after $t_0$ means $f=Q$, by price-time priority.

**Estimating cancellations ahead.** The level is observed as $L(t)$, with trades $v$ between snapshots. Unexplained depletion is $c=\max(0,\,L(t^-)-L(t)-v)$.

| Estimator | Update rule | Evidence class |
|---|---|---|
| Conservative | $\hat C_{\text{ahead}}\equiv 0$ | **E3** |
| Pro-rata (uniform position) | $\hat C_{\text{ahead}} \mathrel{+}= c\cdot R(t^-)/L(t^-)$ | E2 (model-dependent) |
| Touch only | none | E1, never admissible |

**States and transitions.** "Contra side" means offers for a buy and bids for a sell.

| From | To | Condition |
|---|---|---|
| SUBMITTED | REJECTED | Price-band, tick, RMS or margin failure |
| SUBMITTED | `LOCKED_NO_CONTRA` | **Buy:** Σ offers = 0 (upper band). **Sell:** Σ bids = 0 (lower band). Rule 4's "Total Bids == 0" is the lock for *sell exits*, not for a resting buy. A buy bid with zero bids is simply alone at the best bid. |
| LOCKED | QUEUED | Contra side reappears. Snapshot $R_0$ again. |
| SUBMITTED | QUEUED($R_0$) | Acknowledged and resting |
| QUEUED | QUEUED($R'$) | Trades or cancellations ahead |
| QUEUED | PARTIAL($f$) | $0<f<Q$ |
| QUEUED/PARTIAL | FILLED | $f=Q$, or a trade-through |
| any live state | EXPIRED/CANCELLED | Time limit (e.g. one bar), session policy or kill switch |
| PARTIAL | position of $f$ shares | **The bracket and the risk reservation must be resized to $f$.** Leftover pending risk is released only when cancellation is acknowledged. |

**Toxicity.** I agree with Codex on markout: $M_h=s\,(m_{\tau+h}-p)/p$. Define toxicity before looking at data, e.g. $M_{60s}<-1$ tick, and estimate $P(\text{toxic}\mid\text{fill},\,\text{fill latency}<\tau_f)$ with a Beta(1,1) prior. Report the count next to the estimate.

The mandate's "fills rapidly without market movement" case needs trade-level timestamps. It cannot be computed from anything the repo records today, because `track2_session_recorder.py` writes only QUOTE and SIGNAL events (my 23-Sep finding).

---

## 3. Section 2: strategy by strategy

### 3.0 Cross-cutting findings (these outweigh any single strategy)

1. **Most trades end at the time exit, not a target.**
   - Of the 103 first ORB breaks, 17% hit the stop, **10% reached T1**, and **73% ended at the 15:00 exit** with a mean of −0.046R.
   - T2 given T1 (q) was 10%, i.e. 1 of 10.
   - The whole T1/T2 design governs about 10% of outcomes. The strategy's result is decided by the drift between entry and 15:00.
2. **Realised risk is not ₹1,500.**
   - The median stop to the OR low is 1.45% (10th percentile 0.83%, 90th 2.25%).
   - The ₹58,333 cap binds on **96%** of trades.
   - Median realised risk is **₹832**, and costs are charged against that smaller risk (c ≈ 0.07R, not 0.04R).
3. **The mandate's expectancy table:**
   - 44 × ₹3,251 − 56 × ₹1,562 = +₹55,572. That requires q = 1 (every winner goes to +3R), R = ₹1,500, and double friction on winners.
   - Measured: ORB through its own code had a 43.9% win rate and a mean of **−0.003R** (95% CI −0.20 to +0.22).
   - At the mandate's own "44% hurdle", this system made nothing.
   - The correct expectancy is a sum over the actual exit branches, with each probability measured, not assumed:

$$
E[R]= -\pi_S(1+c)+\pi_{1}\big[0.75+0.5(q\cdot 3+(1-q)\,\bar R_{2|\neg T2})\big]+\pi_T\,\bar R_T-(1-\pi_S)\,c
$$

   The branches are stop ($\pi_S$), T1 then either T2 or breakeven/time, and time exit ($\pi_T$).

4. **Strategies fire repeatedly.** With no first-break rule, ORB fires on 2.6 bars per signalling stock-day, VWAP_RECLAIM on 3.0 and SQUEEZE on 3.0. After a stop-out, the same setup can re-enter.
5. **Session ATR distorts every "×ATR20" gate** (§1.4). A "1.2 × ATR20" compression test is really about "2 × the typical bar". A "1.4 × ATR20" stretch is really about "2.3 × the typical bar". Use a **time-of-day-bucketed** 15-minute volatility built from prior sessions, the same way RVOL is built.
6. **ER8 cannot tell chop from trend at n = 8.**
   - For a driftless random walk, $E|\sum x|/E\sum|x| = 1/\sqrt 8 = 0.354$.
   - Simulated over 200k paths: mean 0.357, and $P(ER8\ge0.35)=0.45$, $P(\ge0.40)=0.39$, $P(\ge0.45)=0.33$.
   - TRAPDOOR's gate blocked 48% of eligible evaluations in real data, almost exactly the noise rate.
   - The thresholds 0.35 (TRAPDOOR), 0.40 (LAST_LIGHT) and 0.45 (RECOIL) all sit inside the noise distribution.
   - ER over n bars has a noise mean of about $1/\sqrt n$, so a longer window only helps slowly. Set the threshold from the noise distribution instead, e.g. call "trend" only when $ER_n$ exceeds the 90th percentile of simulated noise for that $n$.
7. **Sector labels are not correlation clusters.**
   - Mean pairwise correlation of within-session 15-minute returns is 0.32.
   - The highest pairs cross labels: IREDA–RVNL 0.50 (PSU_RENEWABLE_FINANCE vs PSU_RAILWAYS_INFRA), RVNL–SUZLON 0.47, and BDL–COCHINSHIP 0.42 (DEFENSE_AEROSPACE vs DEFENSE_SHIPBUILDING).
   - On the worst 5% of NIFTY bars, **89%** of the 8 stocks fell in the same bar, by −0.95σ on average.
   - Three equal-risk positions at ρ = 0.32 are worth $N_{\text{eff}} = 3/(1+2\rho) = 1.8$ independent bets.
   - "Max 2 per sector" does not diversify this basket. Cap by correlation cluster: hierarchical clustering on rolling 20-session 15-minute returns, at most one position per cluster above ρ = 0.4.
8. **Statistical power** (σ_R = 0.74, measured):

| True edge | Trades needed (one-sided 5%, 80% power) |
|---|---|
| 0.05R | 1,358 |
| 0.10R | 340 |
| 0.20R | 85 |
| 0.30R | 38 |

   With 20 fills, the 95% interval is **±0.32R**. The "60 sessions / 20 fills" gate cannot tell zero edge from a strong one. This is consistent with the cloud Monte Carlo finding that a zero-edge ensemble passes the gate 48% of the time (`Claude outputs/2026-09-24_monte_carlo_ensemble_audit.md`; I have not re-verified that run).

### 3.1 Measured behaviour of the production code (probe_empirical §C, C2, F)

Setup: 176 stock-days with at least 10 prior sessions. Trend gates are disabled (which only adds signals). Only the first signal per stock-day is traded.

| Strategy | Signal stock-days | Bars firing per signal day | Main blocking gate | n | Mean net R (95% CI) | Hit T1 | T2 given T1 |
|---|---|---|---|---|---|---|---|
| ORB (via `evaluate_15m_orb`, 2.5×) | 41 (23%) | 2.6 | IN_RANGE, VOLUME | 41 | −0.003 (−0.20…+0.22) | 7% | 0% |
| VWAP_RECLAIM | **89 (51%)** | 3.0 | NO_RECLAIM, VOLUME | 89 | **−0.258 (−0.45…−0.05)** | 11% | 60% |
| VOL_SQUEEZE | 13 (7%) | 3.0 | NO_COMPRESSION | 13 | **−0.530 (−0.79…−0.27)** | 0% | — |
| TRAPDOOR | 18 (10%) | 1.2 | NO_PATTERN, REGIME (ER8) | 18 | −0.077 (−0.35…+0.19) | 0% | — |
| LAST_LIGHT | 11 (6%) | 1.1 | TIME_WINDOW, REGIME | 11 | +0.226 (−0.20…+0.73) | 9% | 100% (1 of 1) |
| RECOIL | 5 (3%), of which 3 SELL | 1.4 | NO_PATTERN, REGIME | 5 | −0.346 (−0.96…+0.27) | 0% | — |
| COMPASS (2-stock sectors only) | 17 (10%) | 1.6 | SECTOR_NOT_LEADING | 17 | −0.095 (−0.27…+0.12) | 0% | — |

**Sensitivity (§F).** The first signal per stock-day was re-run at three thresholds each:
- VWAP_RECLAIM at volume 1.5/1.8/2.5×: −0.28, −0.26, −0.27. All three intervals exclude 0.
- VOL_SQUEEZE at 1.5/2.0/3.0×: −0.51, −0.53, −0.44. All three intervals exclude 0.
- ORB at 2.0/2.5/3.5×: −0.08, −0.00, −0.15.
- TRAPDOOR at RVOL 1.0/1.3/1.8: −0.13, −0.08, −0.19.
- LAST_LIGHT at ER8 0.30/0.40/0.50: +0.26, +0.23, +0.23 (n = 12, 11, 8; all intervals include 0).
- RECOIL at stretch 1.2/1.4/1.6: n = 5 each time. The stretch threshold never binds.
- COMPASS at RVOL 1.2/1.5/2.0: −0.07, −0.10, −0.16.

That is 21 configurations; at 5% significance, one false positive is expected by chance. **No parameter should be chosen from this table.** Its only legitimate reading is that VWAP_RECLAIM and VOL_SQUEEZE as coded are structurally unprofitable here, and nothing is demonstrably profitable.

### 3.2 ORB_MOMENTUM

- **What the code does** (`track2_alpha_engine.evaluate_15m_orb` and `track2_orb_signal_adapter`):
  - Enters when price is above the 09:15 bar's high, within 0.5 × daily ATR14.
  - Requires volume ≥ the regime multiple (2.5×, but 3.5× in practice because breadth is never passed; my 23-Sep finding).
  - Stop from the screener is max(OR low, entry − 1.5 × ATR).
  - Notional cap is ₹1,00,000, not ₹58,333.
  - There is no first-break rule, and the adapter takes the latest bar from 09:30 to 14:30.
- **Failure modes:**
  1. **The opening range is the most volatile bar of the day** (09:15 range 1.25% vs 0.25% midday, 5×). A stop at the OR low is therefore wide (median 1.45%), so the cap binds and T1 needs about +2.2%. T1 was hit 10% of the time before 15:00.
  2. **Re-firing.** It signals on 2.6 bars per signal day, and late breaks (after 13:00) have almost no time to work.
  3. **Monday or gap days.** The OR includes the gap-open auction print. On a big gap the OR high is a news price, not a supply level. This wasn't tested (the sample is too small to split).
- **Parameter sensitivity:** volume 2.0/2.5/3.5× give −0.08/−0.00/−0.15R. The volume gate is not what creates or removes edge here.
- **RVOL baseline:** the mandate is right that the baseline must be time-of-day bucketed. The desk does bucket it (`track2_daily_paper_desk.py:368`), but its baseline file has no writer (my finding N29), so the gate currently fails every cycle.
- **OFI / absorption defence:** `evaluate_microstructure_defense` fails **open**. On any exception it returns `is_distribution_trap=False` (`track2_alpha_engine.py:358-364`), and it uses a fixed tick of 0.05. The 0.20 and 2.50 thresholds have no calibration data behind them.
- **Fixes:**
  - Add a first-break rule and a time window (break by 11:00).
  - Stop = max(OR low, entry − $z\,\hat\sigma_{15}(\tau)\sqrt{h}$), with $\hat\sigma_{15}(\tau)$ bucketed by time of day.
  - Treat the 15:00 exit as the *primary* payoff and measure drift-to-close. Treat T1/T2 as optional.
  - Make the OFI defence fail closed.

### 3.3 VWAP_RECLAIM

- **What the code does differs from its own specification:**
  1. **No reclaim is required.** There is no condition that price was *below* VWAP before. `prior_low <= prior_vwap × 1.005` is satisfied by a stock 0.4% *above* VWAP that never touched it. `curr_close > vwap` then fires on ordinary continuation.
  2. The pullback tolerance is 1.005 in code, not the 1.002 in its docstring.
  3. The "held" condition is `prior_close >= lower_band × 0.990`, which is 1% below the lower band.
  4. There is no time window, so it fires at 14:45 with 15 minutes to the flat.
  5. There is no maximum risk.
  6. Notional is capped at ₹2,00,000.
- **Measured:** it fires on **51%** of stock-days, 3 bars per day. Mean −0.26R, with intervals below zero at every volume threshold.
- **Failure mode:** it is a "price near VWAP with volume" filter that buys the midday drift on distribution days. The mandate's own failure mode, counter-trend pullbacks turning into sell-offs, is the one being bought.
- **Fixes:**
  - Require $C_{t-1} < VWAP_{t-1}$ and $C_t > VWAP_t$ (an actual cross from below), or a low below VWAP with the close back above it.
  - Pullback depth ≤ 1.002.
  - Structural stop = min(pullback low, VWAP × 0.9975). This is the mandate's proposal and I agree.
  - Window 09:45 to 13:30. Maximum risk 1.5%. Cap at the slot.
  - Then retest on new data. Do not re-tune on this sample.

### 3.4 VOLATILITY_SQUEEZE

- **What the code does:**
  - Compression is scored from yesterday's daily bar (NR7 0.45, inside day 0.30, BB inside KC 0.25), with a threshold of 0.30. BB inside KC alone therefore never qualifies.
  - Trigger: *any* 15-minute bar at *any* time that closes above yesterday's high. There is no first-break rule and no window.
  - Stop at yesterday's midpoint, with **no maximum risk**. Cap ₹2,00,000.
  - Whether `daily_candles` includes today's partial bar is unspecified, which risks lookahead.
- **Measured:** mean −0.53R with the interval below zero, and **0% reached T1**.
- **Why it fails:**
  - A stop at the midpoint means risk = ½ × yesterday's range + (close − yesterday's high). On a gap-and-go day that is large, so T1 = 1.5R is unreachable before 15:00.
  - The "coiled spring" is priced into the gap before any 15-minute bar can confirm it.
- **Fixes:**
  - Evaluate only the first close above the compression high, within 09:30 to 11:00.
  - Cap risk at 1.5%.
  - Require compression to be point-in-time (yesterday's bar complete, today excluded).
  - If the gap already exceeds 0.5 × yesterday's range, skip.
  - Retest.

### 3.5 TRAPDOOR

- **It cannot run in the ensemble** (`evaluate_setup` doesn't exist; §1.3).
- **Code versus specification:**
  - The docstring's "within 2 bars" windows are not enforced.
  - `market_breadth` is accepted and ignored.
  - The confirmation doesn't have to be the *first* close above the inside high.
  - The single 1.8R target and 4-bar time stop are overridden by the engine (1.5R/3.0R).
- **Measured:** −0.08R, with T1 (1.8R) hit 0 times in 18 trades. In this sample the 4-bar time stop decides nearly every outcome.
- **The ER8 gate is noise** (§3.0.6).
- **The mother-bar test (≤ 1.2 × ATR20) is lax** because session ATR is 1.6 to 1.75× the typical bar, so "compression" means "not a huge bar".
- **Failure mode:** a legitimate breakdown whose "failed probe" is one wick in a falling market. With breadth ignored, nothing separates that from a trap.
- **Friction:** the "bug fix" the mandate asks for changes nothing here (§1.1).
- **Fixes:**
  - Enforce the bar windows and the first-cross rule.
  - Use the breadth input.
  - Replace ER8 with a noise-calibrated threshold.
  - Use a bucketed volatility unit.
  - Keep the strategy's own 1.8R single target and time stop in the engine.

### 3.6 LAST_LIGHT

- **What the code does:**
  - Timestamps are bar *starts*, so the 14:00 to 14:30 window means signals are known at 14:15, 14:30 and 14:45.
  - The time left before a 15:10 flat is 25 to 55 minutes. On Dhan, 15:10 is the broker's own square-off, so the working deadline is about 15:05 to 15:08.
  - Parsing fails open on timestamps without a `T` (§1.4).
- **Measured:** it is the only positive result, +0.23R, but n = 11 and the interval is −0.20 to +0.73. **That is not evidence.**
- **The +3.0R runner is decorative.**
  - Minimum risk is 0.50%, so T2 needs a ≥ 1.5% move in under 55 minutes.
  - Afternoon 15-minute bars average about 0.26% range. Even perfect trending (ER = 1) gives roughly 4 bars × 0.26% ≈ 1% by 15:05.
  - T2 is essentially out of reach by construction. T1 (≥ 0.75%) is marginal.
  - The mandate's proposal (T1 = 1.2R on 60%, T2 = 2.0R on 40%) moves in the right direction but keeps the same fiction.
- **Fix:** model it as a **time-exit strategy**: enter on the breakout, exit at 15:05, no targets, stop only. Its expectancy is then simply E[drift from 14:15–14:45 to 15:05 | signal] − c. That can be estimated directly, and it is the quantity that actually determines the result.
- **Regime risk:** the index-rebalancing and benchmark-tracking story is untested. Also, under CAS the 15:00 to 15:15 VWAP window sets the closing price, so late-session flow partly moves into the auction. Watch for continuation into 15:00 weakening after CAS.

### 3.7 RECOIL

- **Effectively disabled:**
  - 3 of its 5 first signals were SELL, which the governor rejects (`INVERTED_STOP`).
  - `UnifiedTradeSignal` carries no side, so downstream code treats every signal as a long.
- **Double counting matters here** (3 of the 5 cost rejections flip). But the binding gates are ER8 (noise), the 0.50% risk floor and the wick ratio. The stretch threshold made no difference between 1.2 and 1.6.
- **Payoff geometry:**
  - With the target at VWAP and the stop 0.1 × ATR beyond the extreme, $R{:}R = (s\,A - x)/(x + 0.1A)$, where $s$ is the stretch at the extreme and $x$ is the close's distance from the extreme.
  - For $s=1.4$ and $x=0.6A$: R:R = 0.8/0.7 ≈ **1.14**.
  - Breakeven win rate is $p^\ast=(1+c)/(1+R{:}R)\approx 0.49$ to $0.52$ after costs.
  - It needs better than a coin flip on fades in stocks that just printed 2.2× volume.
- **Self-reference:** the ATR used to measure the stretch includes the climax bar.
- **Fixes:**
  - Keep it disabled until short-side plumbing exists end to end (side field, governor, fees and short-sale MIS rules).
  - Measure stretch in bucketed σ units that exclude the current bar.
  - Test the BUY side alone first.

### 3.8 COMPASS

- **It is not wired** (§1.3). In the sample only two 2-stock "sectors" exist, so the "sector" is effectively one peer.
- **Measured:** −0.10R, with 0% reaching the 2.0R target.
- **Fallbacks** (Codex also found these): missing peers become the candidate itself, a missing market becomes return 0, and beta defaults to 1 within [0.5, 2.5].
- **Candidate in its own sector average:** if the caller includes the candidate among the constituents, a 3-stock sector puts a third of the weight on the candidate, biasing RS toward zero.
- **Docstring vs code:** the docstring says breadth ≥ 60%; the code uses 50%.
- **Beta:** a rolling $\beta_{i,s}=\mathrm{Cov}(r_i,r_s)/\mathrm{Var}(r_s)$ over 20 sessions of 15-minute returns, excluding the first bar, is the right estimator. With ρ ≈ 0.3 to 0.5 between names (§3.0.7) it will be noisy, so shrink it toward 1: $\tilde\beta = w\hat\beta + (1-w)$, $w = n/(n+n_0)$.

### 3.9 Arbitration matrix

- **ORB + SQUEEZE confluence (+0.05 conviction):** the conviction score is not a calibrated probability, so +0.05 has no unit. Test confluence as a separate stratum (both vs ORB only) with its own counterfactual ledger.
- **ORB long vs RECOIL short:** moot, because shorts are blocked. The conflict rule should be "no trade in that symbol" until a validated regime selector exists (I agree with Codex).
- **Ranking:** because of the fixed volume scores (§1.4), today's `rank_and_allocate` systematically prefers VWAP_RECLAIM and the three fixed-score strategies over ORB. Rank on estimated net expectancy per rupee of risk, with a correlation-cluster cap (§3.0.7). Otherwise rank equally until estimates exist.

---

## 4. Section 3: derivatives overlay

I agree with Codex's §3.1 and §3.2 on the corrected GEX formula, the fact that OI walls don't prove call writing, Max Pain not proving pinning, PCR thresholds, dividend-adjusted basis and Tuesday expiry. What follows adds to that.

### 4.1 Dealer GEX sign

$$
GEX_{1\%}=0.01\,S^2\sum_i \sigma^{dealer}_i\,\Gamma_i\,OI_i\,L_i,\qquad \Gamma_i=\frac{\varphi(d_{1,i})}{S\,\sigma_i\sqrt{T_i}}
$$

Here $L_i$ is the **lot size**, not the US ×100.

1. **The sign can't be identified per stock from public data.**
   - Public per-strike data is total OI, which has no side.
   - To my knowledge, NSE's participant-wise OI file (Client/DII/FII/Pro, long/short, for stock calls and puts) is published end of day, **aggregated across all stocks**. Verify against NSE's current file before relying on it.
   - The US convention (customers long puts and short calls, so dealers are long calls and short puts) is untested for Indian single stocks, where individuals are predominantly option buyers.
   - With $\sigma^{dealer}$ unknown, the only defensible statistic has no sign, and it should be scaled to the stock's own liquidity:

$$
G^{abs}=0.01\,S^2\sum_i\Gamma_i\,OI_i\,L_i,\qquad \text{hedge-flow share}=\frac{G^{abs}\times\text{(expected \% move)}}{ADV_{₹}}
$$

   If a 1% move would require hedging flow below about 1% of daily turnover, gamma cannot plausibly dominate intraday price. Compute this first. For most F&O stocks outside the top 30 by options OI, I expect it to fail. That is a hypothesis to test, not a finding.

2. **Physical settlement.** Stock options and futures in India settle physically. Near expiry, in-the-money positions are closed to avoid delivery (many brokers force-close them before expiry). Expiry-week OI falls and price/OI "unwinding" labels are therefore driven by settlement mechanics. Exclude the last three sessions before expiry from any OI-based feature, or model them separately.

3. **Gamma needs an IV per strike.** Stale option LTPs in illiquid strikes produce bad IVs, which produce bad gamma. Near expiry, ATM gamma → ∞ as $T\to0$, so GEX is dominated by one or two ATM strikes on expiry day. Use mid-quote IV with a staleness cutoff, and report the share of GEX from the top two strikes.

### 4.2 Strike OI walls

- `argmax OI` is unstable. It jumps between neighbouring round strikes on small changes and has ties.
- Use a concentration measure instead: $C_K = OI_K/\sum_{|K'-S|\le 2\sigma_d S} OI_{K'}$. Call it a wall only when $C_K$ is above its own 90th percentile over the last 60 sessions.
- **The proximity filter is not defined.** The mandate's "within 0.35 × ATR" doesn't say which ATR. Use daily ATR14; the session 15-minute ATR is time-biased (§1.4).
- **Test design:** among the existing signals, compare outcomes within 0.35 × ATR_d of a wall versus not. Count the missed winners in the excluded group. On the power table (§3.0.8), a 60-session sample cannot resolve this.

### 4.3 Max Pain

- Single-stock options expire monthly (last Tuesday, per Codex's NSE citation). Max Pain therefore matters on about one session in 20.
- A 60-session paper window contains about **3** expiry days, which is zero power.
- Park it until a multi-year history exists.

### 4.4 Futures basis

The noise floor must sit above the threshold:

$$
|B^{resid}_t| > \tfrac12\,(S^{fut}_{bps}+S^{cash}_{bps}) + z\,\sigma_{1s}\sqrt{\Delta t_{async}}
$$

- A threshold of −0.05% (5 bps) is **one tick** on a ₹100 stock with a ₹0.05 tick. It sits inside quote noise for most names.
- **Annualisation blows up near expiry.** At DTE = 1, a one-tick (5 bp) change is an 18% annualised carry change. Don't annualise below DTE = 5. Use the unannualised residual.
- **Use synchronised mid-quotes for both legs.** LTP basis mixes prints that are seconds to minutes apart in thinner futures.
- **Exclude dividend windows** (Codex's fair-value formula handles this).

### 4.5 Sequencing

None of these data sources exists in the repo, and the equity feed itself is not live yet. Each overlay is a new free parameter on a base with no demonstrated edge, which multiplies the testing burden in the power table. **Order:** base execution and evidence first, then one strategy with positive forward net expectancy, then overlays as pre-registered challengers with their own power calculation.

---

## 5. Mandate premises that are false or unsupported

| Mandate statement | Status |
|---|---|
| "Production-grade desk with 815 passing tests" | False. The ensemble crashes, COMPASS is unwired, the bracket manager is unused, and no daemon calls the engine. Tests pass because none of them exercise these paths. |
| "Strict ₹58,333 slot ceiling" | Not enforced. ₹1,50,000 was approved in the probe; caps of ₹2L and ₹1L exist in the code. |
| "₹1,500 risk per trade" | Nominal only. Median realised risk is ₹832 for ORB because the cap binds 96% of the time. |
| "44.0% hurdle two-tranche model" | 44% is roughly the single-target breakeven. The two-tranche breakeven depends on q, and q was measured at about 10%. |
| "+₹55,572 per 100 trades" | Requires q = 1, R = ₹1,500 and double friction on winners. Measured ORB at 43.9% wins gave −0.003R. |
| "Zerodha/Dhan RMS square-off at 15:12" | Wrong for Dhan, which is 15:10 (dhan.co, fetched today). |
| "CAS 15:15–15:30, matching 15:23–15:28" | Wrong according to Codex's NSE citation (15:15–15:35). I couldn't independently confirm because the NSE fetch timed out. |
| "Prove P(shortfall) ≡ 0" | Not a proof. I agree with Codex §1.5. |
| RECOIL short fades are live | Blocked by the governor. `UnifiedTradeSignal` has no side. |
| Friction "systemic bug" across four files | Arithmetically true. It changes behaviour only in RECOIL. |
| VIX tiers, PCR 1.40/0.65, "false breakout rate > 65%", OFI 0.20, absorption 2.50 | Uncalibrated numbers presented as facts. |
| Persona ("former Goldman / Tower lead") | Adds no evidence. Audits are judged on reproducible probes. |

---

## 6. Required actions, in order (no production edits made; owners per Rule 8)

1. **P0: make the ensemble executable or delete it.**
   - Fix the method names (`evaluate_15m_orb` / `evaluate`), or add adapter methods.
   - Add an integration test that runs `evaluate_symbol` over a full 25-bar session and asserts no exceptions.
   - Decide and record whether the desk path is the ensemble or `track2_orb_signal_adapter`. Today it is the adapter.
2. **P0: one session policy object**, consumed by every strategy, simulator, daemon and the UI.
   - Broker-specific RMS time (Dhan 15:10).
   - Exits start at 15:03, escalate at 15:06, flat and reconciled by 15:08.
   - `is_cas_eligible` defaults to True for F&O equities.
   - No default `product_type`; missing time means fail closed.
3. **P0: evidence symmetry.** Stops and time exits need evidence, or are tagged `MODELLED_NOT_EVIDENCED` and excluded from qualification.
4. **P1: governor.**
   - Enforce the ₹58,333 per-slot cap, or say explicitly that it is not a cap.
   - Remove the per-strategy notional caps.
   - Add a correlation-cluster cap (at most one per cluster with ρ > 0.4).
5. **P1: one fee function**, called with an explicit product type. Delete the four hard-coded friction constants.
6. **P1: feature hygiene.**
   - Volatility by time-of-day bucket (not session ATR).
   - Noise-calibrated ER thresholds.
   - Remove RVOL's 1,000 floor and fail closed.
   - Parse LAST_LIGHT times from a typed timestamp.
   - First-break rules for ORB, VWAP_RECLAIM and SQUEEZE.
7. **P1: VWAP_RECLAIM and VOL_SQUEEZE.** Rewrite per §3.3–3.4, or retire them. As coded they lost money in the only data available.
8. **P1: evaluation design.**
   - A counterfactual ledger: every signal, filled or not, clamp aborts included.
   - A gate sized by power, not by a count of sessions and fills. At the measured σ_R = 0.74, show a one-sided lower confidence bound above 0 at the pre-registered sample size.
   - Pre-register one strategy. Do not select among seven after the fact.
9. **P2: derivatives overlays**, only after item 8 shows a positive lower bound for a base strategy.

---

## 7. Questions only Yashu can answer

1. **Broker for Track 2 execution: Dhan or Zerodha?** The RMS clock (15:10 vs 15:12), the square-off fee and the API differ. The session policy can't be finalised without this.
2. **MIS or CNC?** The strategies filter on MIS costs and the brackets default to CNC. The two differ by a factor of 2.1 in round-trip cost. The "₹75,000 T+1 delivery buffer" only makes sense for CNC.
3. **Which path is the desk: the old ORB adapter or the seven-strategy engine?** Today only the adapter is wired, and the engine cannot run.
4. **Will you accept the power table as the gate?** At realistic edges (0.1 to 0.2R), qualification needs 85 to 340 trades, not 20.
