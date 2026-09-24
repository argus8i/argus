# Track 2 Ensemble: Monte Carlo Stress Audit and Rule 4 Fill Microstructure

**Author:** Claude (Rule 8: microstructure, adverse selection, quantitative red-team)
**Date:** 24 Sep 2026 (cloud session)
**Scope:** Track 2 only (Rule 11). Paper research (Rule 1). No file under `antigravity/` was changed.
**Artifacts:**
- Simulator: `research/execution_realism/monte_carlo_ensemble_audit.py` (standard library only)
- Tests: `research/tests/test_monte_carlo_ensemble_audit.py` (10 tests)
- Raw results: `shared/track2_liquid/monte_carlo_ensemble_audit_results.json` (seed 20260924, 10,000 paths x 250 sessions, 218 s)

Reproduce with `python -m research.execution_realism.monte_carlo_ensemble_audit --paths 10000`.

---

## 0. Verdict

1. **The 44.0% breakeven does not hold for the two-tranche exit as it runs intraday.** 44.0% is the breakeven for a *single* 1.5R exit at a 1.0% stop (44.24%). For the two-tranche model it requires 46–51% of Tranche-1 winners to reach 3.0R in the same session. With 15-minute volatility measured from the repo's own candles, the simulated share is **10.4% for ORB and 3.2% for VWAP Reclaim**. 60–74% of those trades end at the 15:10 hard flat, not at a target.
2. **Every strategy needs about 0.09–0.11R of gross edge per trade just to break even.** With zero drift, net expectancy is −0.089R to −0.112R per trade. That is the ₹61.83 friction, plus spread and stop slippage, divided by a 1R of only ₹583–₹875.
3. **Passive entries are adversely selected (base-case priors).** A BUY limit at the breakout level is filled on 98.6% of distribution traps but only 32.8% of genuine breakouts. 75% of passively filled quantity comes from failed breakouts. In the ensemble this turns ORB from +0.115R to **−0.206R** per trade and VWAP Reclaim from +0.094R to **−0.147R**. The direction is robust across the prior grid, but the size is not (§4.4).
4. **The 60-session paper gate cannot tell edge from no edge.** A zero-edge ensemble passes Rule 1 as written ("≥20 fillable entries, positive net expectancy") **48.0%** of the time. A genuine +0.10R edge passes 88.8% of the time, but reaches 5% significance only **35.9%** of the time.
5. **Two of the six strategies do not exist.** LAST LIGHT and RECOIL appear nowhere in the repository. Their rows below are placeholders and must not be read as evidence. COMPASS and TRAPDOOR exist but are not wired into `MultiStrategyEngine`.

**Recommendation:** no core-model change on the strength of this audit alone (Rule 8). Items R1–R6 in §8 go to Antigravity and Codex for review.

---

## 1. Test suite

```
pytest tests/test_track2_new_strategies.py tests/test_track2_multi_strategy_engine.py
7 passed in 0.08s
```

New module: `pytest research/tests/test_monte_carlo_ensemble_audit.py` gives **10 passed**. These tests caught one bug in my own closed-form breakeven, which was fixed before the full run.

Wider sweep, for context only (nothing here is caused by this change): 755 passed, 5 failed, and 5 modules fail to collect.
- **Collection errors:** three modules need `websockets`, which is not installed here. Two import `antigravity/orchestrator/coordinator.py:465`, which uses an f-string that only Python 3.12 accepts; this container runs 3.11.
- **Pre-existing failures:** 3 × `NameError: Optional` in `test_track1_historical_engine.py`, plus `test_tri_agent_messaging.py::test_path_traversal_rejection` and `test_depth_bridge_failclosed.py::test_enforce_tab_focus_consumes_two_ids`.

---

## 2. Code-level findings (verified in source, independent of the simulation)

| # | Severity | Finding | Evidence |
|---|---|---|---|
| F1 | High | **LAST LIGHT and RECOIL are not implemented anywhere.** A six-strategy ensemble cannot be audited. | `grep -ri "last.?light\|recoil"` finds no matches outside this audit |
| F2 | High | **No six-strategy ensemble exists in code.** `MultiStrategyEngine` ensembles ORB, VWAP Reclaim and **Volatility Squeeze**. COMPASS and TRAPDOOR are standalone classes that no engine calls. | `antigravity/models/track2_multi_strategy_engine.py:71-73` |
| F3 | High | **Notional caps differ by strategy, and the ₹58,333 slot is not enforced per trade.** COMPASS and TRAPDOOR use ₹58,333. ORB uses ₹1,00,000 (`liquid_momentum_screener.py:348`). VWAP Reclaim and Squeeze use ₹2,00,000 (`track2_vwap_reclaim_strategy.py:97`, `track2_volatility_squeeze_strategy.py:92`), and `MultiStrategyEngine` does not override them. Worked example: VWAP at a ₹1,000 entry with a 1.25% stop sizes to 120 shares (₹1.2L notional, ₹1,500 risk). The slot cap would give 58 shares (₹725 risk). The governor enforces only a *total* ≤ ₹1.75L, so one VWAP signal can take 69% of deployable notional. | Checked by instantiating `MultiStrategyEngine()` |
| F4 | Medium | **The ₹1,500 risk budget never binds in the audited 1.0–1.5% stop band.** Under the ₹58,333 cap, 1R = ₹583–₹875 and friction = 0.071–0.106R. Any R-based hurdle quoted against ₹1,500 understates friction by a factor of 1.7–2.6. | `min(1500/rps, 58333/entry)` |
| F5 | Medium | **COMPASS and TRAPDOOR targets are unreachable at 1.0–1.5% stops.** A 2.0R or 1.8R target needs a 1.8–3.0% move within 4–6 bars. Measured 1-hour σ is about 0.53%, so the simulated target hit rate is **0.1–0.4%** and 93–98% of trades end on the time exit. Their own stop rule (prior 3-bar low − 0.1·ATR20, bounded 0.40–2.50%) can be tighter than the audited band. The schedule and the strategy geometry contradict each other. | §3, `simulated_hurdles` |
| F6 | Low | ₹61.86 as quoted implies 0.10605%. 0.106% × ₹58,333 = **₹61.83**. The difference is immaterial. | |

---

## 3. The 44.0% breakeven hurdle

**Closed form** (T1 at 1.5R; runner to 3.0R or a breakeven stop; no time exit). With p = P(T1), q = P(runner reaches 3.0R | T1) and f = friction in R:

$$p_{BE} = \frac{1+f}{1.75 + 1.5\,q}$$

| Stop | f (R) | p_BE at q=0 | at q=0.5 | at q=1 | q needed for 44.0% | Single 1.5R exit |
|---|---|---|---|---|---|---|
| 1.00% | 0.106 | 63.2% | 44.2% | 34.0% | **0.509** | **44.2%** |
| 1.25% | 0.085 | 62.0% | 43.4% | 33.4% | 0.477 | 43.4% |
| 1.50% | 0.071 | 61.2% | 42.8% | 32.9% | 0.456 | 42.8% |

A value of q = 0.5 is exactly the zero-drift gambler's-ruin probability of going from +1.5R to +3.0R before falling back to 0, *with unlimited time*. The 44% figure therefore assumes an unlimited holding period. MIS trades don't have one.

**Simulated** (empirical σ, t(5) tails, 15:10 hard flat, drift calibrated so ideal net expectancy = 0):

| Strategy | Breakeven net win rate | P(T1 or target) | Runner q | Time-exit share | Stop share |
|---|---|---|---|---|---|
| ORB (1.5R/3.0R, 22 bars) | 46.3% | 22.3% | **0.104** | 60.2% | 37.1% |
| VWAP Reclaim (1.5R/3.0R, 14 bars) | 47.7% | 12.6% | **0.032** | 74.4% | 25.1% |
| COMPASS (2.0R, 6 bars) | 49.9% | 0.2% | – | 93.3% | 6.5% |
| TRAPDOOR (1.8R, 4 bars) | 51.3% | 0.1% | – | 97.4% | 2.5% |
| LAST LIGHT (placeholder) | 50.3% | 1.1% | – | 94.5% | 4.4% |
| RECOIL (placeholder) | 49.1% | 0.7% | – | 88.0% | 11.3% |

Only 2.3% of ORB trades reach T2. The trade outcome is set mostly by where price sits at 15:10, not by the 1.5R/3.0R ladder. A win-rate hurdle is therefore the wrong control variable. The usable hurdle is **gross edge per trade ≥ 0.09–0.11R** (cost-only expectancy: COMPASS −0.101, TRAPDOOR −0.094, ORB −0.094, VWAP −0.089, LAST LIGHT −0.096, RECOIL −0.112R).

---

## 4. Rule 4: discrete 4-state fill microstructure on 15-minute breakouts

### 4.1 Model
- **Passive BUY limit at the breakout level** (ORB and VWAP Reclaim post `limit_price = current_price`). The order joins the back of a displayed queue of R ahead ~ LogNormal(median 6Q).
- The queue ahead decays by cancellation at rate κ. Sell prints at the level (compound Poisson) consume the queue front first.
- **FILLED** when V_cum ≥ R + Q; **PARTIAL** when R < V_cum < R + Q; **QUEUED** otherwise. A trade-through fills at once and marks the position below the level.
- **LOCKED_NO_OFFER** (the BUY-side lock; dynamic-band freeze with zero offers) has probability 0.3%. Track 2 names have flexing bands, so Track 1 circuit assumptions are not imported (Rule 11).
- **LOCKED_NO_BID** is the SELL-side state. It is modelled on the stop as a 1% SL-limit skip, which is why realized losses can exceed 1R.
- **Distribution trap:** sell flow keeps arriving at the level (an iceberg), and 40% of traps trade through. **Genuine breakout:** flow at the level decays within about 90 s, and bids ahead cancel faster (κ = 1/150 s against 1/600 s).
- **Taker entries** (COMPASS and TRAPDOOR, "next executable ask") pay half the spread plus slippage. Slippage is larger on genuine breakouts (0.03% against 0.01%) because price is moving away.

### 4.2 Terminal-state distribution (20,000 episodes per cell)

| Entry | Breakout | FILLED | PARTIAL | QUEUED | LOCKED | Mean fill fraction |
|---|---|---|---|---|---|---|
| Passive | Genuine | 21.0% | 31.1% | 47.5% | 0.4% | **0.328** |
| Passive | Distribution | 98.4% | 0.4% | 0.9% | 0.3% | **0.986** |
| Taker | either | 99.7% | – | – | 0.3% | 0.997 |

**Adverse-selection ratio: 3.01.** Of passively filled quantity, 25.0% is genuine and 75.0% is trap. Taker fills are unselected (50/50).

### 4.3 Queue-rank decay: P(FILLED by t | initial rank ahead)

| Rank ahead | Genuine 60s / 180s / 300s / 600s / 900s | Distribution 60s / 180s / 300s / 600s / 900s |
|---|---|---|
| 1Q | 15% / 68% / 81% / 85% / 85% | 11% / 98% / 100% / 100% / 100% |
| 3Q | 1% / 11% / 22% / 28% / 30% | 2% / 47% / 99% / 100% / 100% |
| 6Q | 1% / 2% / 4% / 7% / 10% | 3% / 10% / 59% / 100% / 100% |
| 12Q | 0% / 2% / 3% / 5% / 8% | 3% / 8% / 13% / 99% / 100% |
| 24Q | 0% / 1% / 2% / 5% / 8% | 3% / 8% / 14% / 30% / 99% |

Beyond about 3Q of displayed size ahead, a genuine breakout almost never fills. The residual 5–10% comes from trade-throughs. A distribution trap fills with certainty as the iceberg works through the queue. **Time-to-fill is itself a signal:** a fill that arrives after 300 s from rank ≥ 6Q is almost always a trap.

### 4.4 Sensitivity: the direction is robust, the size is not
Across a 24-cell grid (ahead ∈ {1, 3, 6, 12}Q; distribution flow ∈ {0.005, 0.01, 0.02} Q/s; genuine flow ∈ {0.03, 0.10} Q/s), the genuine share of filled quantity ranges from **0.14 to 0.53**. It never rises materially above the unconditional 0.50. Adverse selection disappears only when genuine breakouts also send heavy sell flow back into the level (0.10 Q/s), or when the order sits at the front of the queue. **This is the single most important quantity to measure from Dhan/Kite depth in the paper phase.** Log time-to-fill and queue rank at join for every passive order.

---

## 5. Ensemble Monte Carlo (10,000 paths × 250 sessions)

**Setup:**
- 3 slots at ₹58,333 each; stops uniform on 1.0–1.5%; 0.106% MIS friction on every round trip.
- Each session draws one market factor shared by all trades, plus a 3% shock-day state. All six strategies are long-only.
- Signal arrival rates per session are priors: COMPASS 0.30, TRAPDOOR 0.20, ORB 0.45, VWAP 0.35, LAST LIGHT 0.25, RECOIL 0.25.
- Candidates are ranked randomly. An unfilled passive order frees its slot.
- Scenarios are defined by *ideal-execution* net expectancy. **BREAKEVEN** = 0R per trade. **EDGE** = +0.10R per trade.
- Why not zero drift: the stop/time-exit payoff is convex, so genuine/trap drift dispersion alone yields positive gross P&L. "Zero drift" is therefore not "zero edge".

| Scenario | Trades/session | Net R/trade | ₹/trade | Daily VaR99 | Daily ES99 | 250-session median P&L | P(250-session loss) | Max DD p50 / p95 | Longest losing streak p50 / p95 |
|---|---|---|---|---|---|---|---|---|---|
| BREAKEVEN, ideal fills | 1.72 | −0.003 | −2 | ₹1,740 | ₹1,993 | −₹1,045 | 53.6% | ₹13,672 / ₹26,649 | 8 / 12 |
| BREAKEVEN, spec fills | 1.55 | −0.118 | −86 | ₹1,726 | ₹1,967 | −₹33,157 | 99.9% | ₹35,472 / ₹51,457 | 9 / 13 |
| EDGE, ideal fills | 1.72 | +0.103 | +74 | ₹1,646 | ₹1,883 | +₹32,035 | 0.5% | ₹6,624 / ₹11,876 | 7 / 11 |
| **EDGE, spec fills (Rule 4)** | 1.55 | **−0.024** | **−18** | ₹1,635 | ₹1,866 | **−₹6,712** | **74.8%** | ₹14,338 / ₹27,765 | 8 / 11 |
| EDGE, all-taker entries | 1.72 | +0.074 | +54 | ₹1,688 | ₹1,930 | +₹22,868 | 3.1% | ₹7,667 / ₹14,432 | 7 / 11 |
| EDGE, spec fills, 4 implemented strategies only | 1.10 | −0.066 | −48 | ₹1,540 | ₹1,733 | −₹13,230 | 92.3% | ₹17,409 / ₹30,546 | 7 / 11 |
| EDGE, all-taker, risk-parity sizing | 1.72 | +0.040 | +29 | **₹848** | **₹1,011** | +₹12,378 | 1.7% | ₹3,379 / **₹6,313** | 7 / 11 |

**Per-strategy results under EDGE with spec fills.**

| Strategy | Fill rate | Net R/trade | Net win rate |
|---|---|---|---|
| COMPASS | 99.7% | +0.073 | 55.2% |
| TRAPDOOR | 99.7% | +0.069 | 56.2% |
| ORB | 75.5% | **−0.206** | 41.4% |
| VWAP Reclaim | 75.4% | **−0.147** | 44.9% |
| LAST LIGHT* | 99.7% | +0.066 | 55.4% |
| RECOIL* | 99.7% | +0.075 | 54.1% |

\* Placeholder parameters (F1).

Entry states over 4.35M attempts: FILLED 81.9%, QUEUED 10.8%, PARTIAL 7.0%, LOCKED_NO_OFFER 0.3%.

**Reading the table:**
- **VaR99 and ES99:** a daily VaR99 of about ₹1,650–₹1,740 (0.66–0.70% of the ₹2.5L corpus) is set by three concurrent losing slots. It barely moves with the edge. The single-trade VaR99 is ₹925–₹941. That is above the largest 1R (₹875) because of SL-limit slippage and skips.
- **Drawdown:** with a real +0.10R edge and ideal fills, expect a ₹6.6k median and a ₹11.9k p95 maximum drawdown per year. The p95 longest underwater stretch is 130 sessions.
- **Losing streaks:** the longest losing streak is 7 trades at the median and 11 at p95, even with the edge. Size and psychology should assume **11–13 consecutive losers**.
- **Execution cost:** the gap between ideal and spec fills (+0.103R to −0.024R) is the cost of Rule 4 execution realism under the base-case priors. Moving ORB and VWAP to taker entries recovers most of it (+0.074R).
- **Correlation caveat:** correlation enters only through a daily market factor. Cross-strategy daily P&L correlation comes out at about 0.01–0.02, while measured pairwise 15-minute correlation of these names is about 0.15 (NIFTY ρ ≈ 0.26–0.49). With three concurrent positions, **daily VaR is likely understated by about 10–15%**.

---

## 6. Risk-parity (equal-risk-contribution) capital weights

These weights are computed on the EDGE, all-taker covariance of per-strategy daily P&L. The SPEC-basis weights are not the deployable set, because ORB and VWAP carry negative expectancy under passive entry and should get no capital. The slot cap binds, so a weight can only scale a strategy's slot *down*: multiplier = w / max w.

| Strategy | ERC weight | Slot multiplier | Slot notional | Risk share at equal weight | Risk share at ERC |
|---|---|---|---|---|---|
| COMPASS | 0.174 | 0.642 | ₹37,471 | 9.2% | 16.7% |
| TRAPDOOR | 0.271 | 1.000 | ₹58,333 | 3.9% | 16.7% |
| ORB | 0.075 | 0.276 | ₹16,107 | **45.8%** | 16.7% |
| VWAP Reclaim | 0.104 | 0.383 | ₹22,344 | 24.6% | 16.7% |
| LAST LIGHT* | 0.215 | 0.793 | ₹46,262 | 6.1% | 16.7% |
| RECOIL* | 0.162 | 0.599 | ₹34,965 | 10.4% | 16.7% |

- **ORB and VWAP dominate risk.** At equal slots they carry 70% of ensemble risk: long holding periods, a 1.08R per-trade σ, and the highest signal rates.
- **ERC halves the tail and improves risk-adjusted return:** daily VaR99 goes from ₹1,688 to ₹848, p95 drawdown from ₹14,432 to ₹6,313, and median P&L per unit of p95 drawdown from 1.58 to 1.96. Absolute P&L also halves, because the cap prevents scaling up.
- **These are not diversification weights.** Cross-strategy correlation is near zero in this model, so ERC ≈ inverse volatility. All six strategies are long-only, so on shock days they lose together. The weights depend on placeholder parameters for two strategies and on prior signal rates; recompute them from paper-trade P&L before use.

---

## 7. Can the 60-session paper gate detect edge?

Rule 1: 60 sessions, ≥ 20 fillable entries, verified positive net expectancy. The ensemble produces about 93–103 entries in 60 sessions, so the entry minimum is met on every path.

| True state | P(gate passes as written) | P(passes a one-sided 5% t-test) |
|---|---|---|
| Breakeven, ideal fills | **48.0%** | 4.7% |
| +0.10R edge, ideal fills | 88.8% | **35.9%** |
| +0.10R edge, all-taker | 82.2% | 24.6% |
| +0.10R edge, spec fills (truly negative) | 36.9% | 2.5% |

"Positive net expectancy" over 60 sessions is a coin flip for a strategy with zero edge. Per-trade σ is about 0.8R. Detecting a +0.10R edge at 5% significance with 80% power needs ((1.645 + 0.842) × 0.8 / 0.10)² ≈ **396 trades**. That is about 230 sessions for the whole ensemble, and 4–6× longer per individual strategy. Rule 1's 20-entry floor gives a standard error of about 0.18R, which is larger than any plausible edge.

---

## 8. Recommendations (for Rule 8 review; not implemented)

- **R1 (F1/F2):** stop referring to a "six-strategy ensemble" until LAST LIGHT and RECOIL exist and COMPASS/TRAPDOOR are wired into `MultiStrategyEngine`. Squeeze is in the engine but was not in this audit's scope.
- **R2 (F3/F4):** enforce the ₹58,333 slot cap per trade in `MultiStrategyEngine`, or pass `max_notional_rs=58333` to every sub-strategy. Restate all R-based hurdles against the *binding* 1R (₹583–₹875), not ₹1,500.
- **R3 (§3):** retire "44.0% breakeven" as a two-tranche claim. Track **gross R per trade ≥ 0.10R** and **runner conversion q**. If q stays near 0.1, the 3.0R tranche is decorative and a single time-exit design is honest.
- **R4 (F5):** either widen COMPASS and TRAPDOOR time limits to match 1.0–1.5% stops, or keep their native 0.4–1% stops and accept 0.11–0.27R friction. The current combination has unreachable targets.
- **R5 (§4):** switch ORB and VWAP entries to marketable limits (taker with a price collar), or gate passive fills on time-to-fill and queue-rank features. Before that, measure P(fill | subsequent outcome) from depth logs. It is the least-known and most decisive parameter.
- **R6 (§7):** add a significance criterion to the Rule 1 gate (a one-sided t-test, or a Wilson/bootstrap lower bound on mean net R > 0). Report required sample sizes per strategy instead of a fixed 60 sessions / 20 entries.

---

## 9. Limitations (read before quoting any number)

- **No strategy has an out-of-sample record.** Every drift in this audit is an input calibrated to a target expectancy. The simulation says what happens *if* the edge is X; it cannot say what X is.
- **Calibrated from data:** 15-minute σ (0.266% median), fat tails (t with 5 degrees of freedom; pooled excess kurtosis 6.55), market beta (1.4) and NIFTY daily σ (0.45%). Source: 8 F&O names × 32 sessions in `historical_candles_track2.json`. That is one regime over six weeks.
- **Uncalibrated priors:** queue depth, cancellation and flow rates; genuine/trap split (50/50) and their drift gap (±0.05%/bar); spread and slippage; lock and stop-skip probabilities; signal rates; shock frequency. All are listed in the JSON under `meta.priors`.
- **Not modelled:**
  - path-level intraday co-movement between concurrent positions (so VaR is understated, §5);
  - sector caps;
  - the intraday U-shape in volatility;
  - later passive fills leaving less holding time;
  - flat brokerage on partial fills;
  - overnight risk (MIS is flat by 15:10).
- **Approximations:** a trade-through fill's adverse offset is applied to P&L rather than re-simulating the path from below the level. Stop-then-target ordering within a sub-step is pessimistic, matching `fills.resolve_bar_exit`.
