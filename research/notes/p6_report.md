# P6 report: decision engine (`research/decision/`)

**Date:** 2026-09-25
**Branch:** `track2/decision-engine`
**Author:** Claude (red team)

**Scope:** P6.1–P6.7, built and tested on synthetic data. No real E2/E3 data exists yet, so nothing here says anything about any strategy's edge.

**Tests:**
- `research/tests/test_p6_decision.py`: 32 tests.
- Full research suite: **467 passed**.

**No new dependencies:**
- scipy is not installed, and I did not add it.
- Linkage clustering, the Student-t quantile and the chi² expectation are implemented directly.
- They are checked against published t tables and against Monte Carlo.

## Modules

| Module | What it does | Key tests |
|---|---|---|
| `stats.py` | `clustered_se` (CR1 by session, the same formula as the locked gate's `clustered_t`) and `lb95` | mean / se equals `clustered_t` exactly |
| `ledger.py` | SQLite WAL ledger. UPDATE and DELETE are refused by triggers. Key is (run_id, mode, strategy_version, symbol, decision_ts); `signal_id` is sha1 of those fields. It has the plan's full column list. `rows_from_engine` writes every intent (E1 trades and E1_CF counterfactuals). | duplicate refused; update/delete refused; NaN stored as NULL; hash stable |
| `estimator.py` | `StrategyEstimate`: shrunk_gross = n/(n+85)·mean_gross (**ASSUMPTION** n0 = 85). `expected_net_r` = shrunk − fee_r − slip_r, costed on each signal's own stop distance. Mixing R bases is refused. | a 4× tighter stop gives 4× the fee_r |
| `sizing.py` | A.10: qty cap, and m = clip(VIX_ref/VIX, 0.5, 1). A jump of ≥ 15% gives m = 0.5. Stale (> 5 min), missing or invalid VIX gives m = 0. m is never above 1. | every branch, including bool/str/NaN VIX |
| `clusters.py` | Weekly average-linkage clusters on market-residual 15m correlations at ρ > 0.4, using data strictly before the week. Clusters above 10% of N are split by size-capped complete linkage. Short-history names share `UNCLUSTERED`, which fails closed. | block recovery; a 180-name factor structure with mean ρ ≈ 0.3 never exceeds 18 names; data changes during the week leave that week's clusters unchanged |
| `allocator.py` (`allocate`) | P6.5 conflict rules, SHADOW/EXPLOIT ranking and greedy fill: 3 slots, ₹58,333 cap, free cash, 1 per cluster, 2 per sector. Unknown cluster or sector share one capped bucket. Every drop reason is logged. | rename invariance; each constraint binds; conflicts; EXPLOIT never allocates E ≤ 0 or unpromoted strategies |
| `promotion.py` | The only writer of the register. Holdout transitions need a verified lock and an E1 record that cites it. Futility is a **single look** at the first n ≥ n_pre/2. There is one terminal test at n_pre = 111. Terminal states refuse further looks. DecisionRecord JSON is written every time. | see below |
| `stress.py` | Equicorrelated t-copula over one bar, ≥ 10⁶ draws, fixed seed; mixture formula; band-hit scenario | t quantiles match tables to 2e-4; the Gaussian copula at ρ = 0 is binomial; MC matches the mixture |

## Promotion operating characteristics (MEASURED)

All runs use σ = 0.74 and include the session structure.

| Check | Plan requirement | Measured |
|---|---|---|
| False promotions, true mean 0, n = 85 over 60 sessions, 10,000 simulations | ≤ 3.5% (true rate about 2.4–2.6%) | **2.59%** |
| Power, true mean +0.20R, n = 111 independent trades, 4,000 simulations | 0.80 ± 0.03 | **0.801** |
| Futility, true mean −0.20R, KILLED by n_pre/2, 4,000 simulations | ≥ 60% (plan measured 77%) | **75.7%** |

## Stress grid (MEASURED)

**Setup:** p0 = 1.25% per bar (DERIVED, A.4); 10⁶ draws; seed 20260925.

**Severities are ASSUMPTIONS** until the ledger has data: ₹1,500 per stop, with 5% of stops being gap-throughs at 2×.

| ν | ρ | P(1) | P(2) | P(3) | q99 ₹ | q99.9 ₹ | P(loss > ₹4,500) |
|---|---|---|---|---|---|---|---|
| 3 | 0.30 | 0.0233 | 0.00536 | 0.00118 | 1,500 | 4,500 | 0.00019 |
| 3 | 0.60 | 0.0166 | 0.00612 | 0.00291 | 1,500 | 4,500 | 0.00044 |
| 5 | 0.45 | 0.0234 | 0.00508 | 0.00127 | 1,500 | 4,500 | 0.00020 |
| 8 | 0.30 | 0.0289 | 0.00359 | 0.00043 | 1,500 | 3,000 | 0.00007 |
| 8 | 0.60 | 0.0213 | 0.00543 | 0.00167 | 1,500 | 4,500 | 0.00027 |

The q95 loss is ₹0 in every cell. Run `python -m research.decision.stress` for the full 9-cell grid.

**Finding: the band-hit scenario breaches the tail limit.**
- If all 3 positions are at the ₹58,333 cap, hit a −10% band, and the SL-limit orders go unfilled, the loss is **₹17,500 (7.0% of the ₹2,50,000 corpus)**.
- That is **above Yashu's X = ₹12,000** (decision 5).
- The bar-stop model can never show this, because its per-stop severity is bounded.
- **Options for Yashu:**
  - keep it as an accepted scenario risk;
  - cap total notional at ₹1,20,000 (for example, ₹40,000 per position);
  - allow only 2 full-size positions (₹11,667 in the scenario).

The allocator's constraints are parameters, so any of these is a one-line configuration change, but it needs your decision. **These are model quantiles, not a guaranteed maximum loss.**

## Plan erratum (E-P6.7)

- **What the plan says:** P6.7 says a t-copula with ρ = 0 gives "49–249 times the binomial for 3 of 3 at p0 = 1%".
- **What I measured:** from the mixture formula, **262× (ν = 3), 102× (ν = 5), 40× (ν = 8)**.
- **How it was checked:** 10⁷-draw Monte Carlo gives 255 ± 10 (ν = 3) and 39.8 ± 4 (ν = 8). A 10× finer quadrature grid gives the same values.
- **Conclusion:** the qualitative point (a shared W makes ρ = 0 far from independent) stands. The range is slightly off at both ends. The tests use the measured values.

## Deviations and open items

1. **The engine does not call `allocate`.**
   - `BacktestEngine` still uses its own P2 slot, sector and cluster checks with `DefaultAllocator` ranking.
   - `allocate` (P6.5) is the bar-close allocator for the shadow runner (P8) and the portfolio simulation.
   - Wiring it into the engine is a P7 task, once expected_net_r estimates exist.
2. **Futility is one look, by design.**
   - The plan's state machine is ambiguous about repeated futility checks between n_pre/2 and n_pre.
   - A single look matches the plan's measured 77% and keeps the power at 0.80.
3. **`records/` will hold real DecisionRecords.** Tests write only to `tmp_path`.
4. **Sizing assumptions (+15% jump, 5-minute staleness, 0.5 floor) are not in the trials registry yet.** They are registered with the first study that uses them (P7).
