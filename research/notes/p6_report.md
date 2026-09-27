# P6 report: decision engine (`research/decision/`)

**Date:** 2026-09-25
**Branch:** `track2/decision-engine`
**Author:** Claude (red team)

**Scope:** P6.1–P6.7, built and tested on synthetic data. No real E2/E3 data exists yet, so nothing here says anything about any strategy's edge.

**Tests:**
- `research/tests/test_p6_decision.py`: 32 tests.
- `research/tests/test_adjusted_a1.py`: 42 tests (10 by Codex, 32 added by Claude).
- Full research suite: **509 passed**. Exact commands and exit codes are in section "Adjusted A1: verification".

**Update, 25 Sep 2026:** Yashu adopted **Adjusted A1**. It replaces the plan's ₹58,333.33 slot cap throughout `research/decision/`. See section "Adjusted A1" below. The superseded ₹17,500 band finding is kept at the end for the record.

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
| `sizing.py` | A.10: qty cap, and m = clip(VIX_ref/VIX, 0.5, 1). A jump of ≥ 15% gives m = 0.5. Stale (> 5 min), missing or invalid VIX gives m = 0. m is never above 1. **The single source of the Adjusted A1 constants** (`MAX_SLOTS`, `SLOT_CAP_RS`, `AGGREGATE_EXPOSURE_CAP_RS`, `RISK_BUDGET_RS`). `base_qty` returns 0 for any invalid input. | every branch, including bool/str/NaN VIX; invalid `base_qty` inputs |
| `clusters.py` | Weekly average-linkage clusters on market-residual 15m correlations at ρ > 0.4, using data strictly before the week. Clusters above 10% of N are split by size-capped complete linkage. Short-history names share `UNCLUSTERED`, which fails closed. | block recovery; a 180-name factor structure with mean ρ ≈ 0.3 never exceeds 18 names; data changes during the week leave that week's clusters unchanged |
| `allocator.py` (`allocate`, `ExposureBook`) | P6.5 conflict rules, SHADOW/EXPLOIT ranking and greedy fill under Adjusted A1: 3 slots, ₹38,000 per position, a ₹1,14,000 aggregate on absolute notional (active **plus pending**), free cash, 1 per cluster, 2 per sector. Unknown cluster or sector share one capped bucket. Every drop reason is logged. `ExposureBook` tracks reservations, partial fills, cancels and closes. | rename invariance; each constraint binds; conflicts; EXPLOIT never allocates E ≤ 0 or unpromoted strategies; see the Adjusted A1 section |
| `promotion.py` | The only writer of the register. Holdout transitions need a verified lock and an E1 record that cites it. Futility is a **single look** at the first n ≥ n_pre/2. There is one terminal test at n_pre = 111. Terminal states refuse further looks. DecisionRecord JSON is written every time. | see below |
| `stress.py` | Equicorrelated t-copula over one bar, ≥ 10⁶ draws, fixed seed; mixture formula; deterministic −10/−15/−20% scenarios at the aggregate cap against the ₹12,000 −10% scenario budget, plus the verdict | t quantiles match tables to 2e-4; the Gaussian copula at ρ = 0 is binomial; MC matches the mixture; scenario arithmetic and verdict |

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

This grid does not depend on the slot cap, because its severities are the ₹1,500 per-stop ASSUMPTION. It is therefore unchanged under Adjusted A1. **These are model quantiles, not a guaranteed maximum loss.**

## Adjusted A1 (Yashu, 25 Sep 2026)

### Configuration

| Parameter | Value | Where |
|---|---|---|
| `MAX_SLOTS` | 3 | `research/decision/sizing.py` (the single source; `allocator.py` and `stress.py` import it) |
| `SLOT_CAP_RS` | ₹38,000.00 (was ₹58,333.33) | same |
| `AGGREGATE_EXPOSURE_CAP_RS` | ₹1,14,000.00 | same |
| `RISK_BUDGET_RS` | ₹1,500 maximum planned risk per trade (unchanged) | same |

### What is enforced, and where

**Aggregate cap:**
- `allocate()` sums the **absolute** notional of active positions, pending entry reservations and the candidate.
- Long and short exposure are added, never netted. A candidate that would exceed ₹1,14,000 is dropped as `AGGREGATE_EXPOSURE_CAP`.

**Pending orders:**
- They take a slot and count toward exposure.
- A symbol with a pending entry is refused as `PENDING_ENTRY`.
- A partially filled order appears in both lists but counts as **one** slot.

**Partial fills (`ExposureBook`):**
- A fill moves filled-qty × reservation price out of pending and filled-qty × fill price into active.
- The unfilled remainder stays reserved until it is **cancelled**. That releases only the remainder, and the slot is kept while shares are held.
- Exits release active exposure pro rata, and the slot frees at zero.
- A fill worse than the reservation price (for example, a short filled higher) can push exposure above the cap. A fill is a fact, so it is not refused; it is recorded in `breaches`, and every new reservation is refused until exposure is back under the cap.

**Invalid inputs fail closed:**
- NaN/inf/negative/None/bool booked notionals, free cash or caps raise `ValueError`.
- An invalid candidate notional is dropped as `INVALID_NOTIONAL`.
- `base_qty` returns 0.
- `ExposureBook` refuses bad qty, price, side, duplicate ids and double-booked symbols.

**Sizing:**
- qty = floor(min(1500 / risk per share, 38,000 / entry, free cash / entry)).
- With typical 1.0–1.5% stops, planned risk is about ₹380–570 per trade. The ₹1,500 budget binds only for stops wider than 3.95% (1,500 / 38,000). DERIVED.

### Deterministic scenarios, before costs

The whole book is at the aggregate cap of ₹1,14,000 absolute notional. Every position moves against it by the shock, stops go unfilled, and the exit is at the moved price. Corpus is ₹2,50,000.

| Scenario | Loss | Share of corpus | Against the ₹12,000 −10% scenario budget |
|---|---|---|---|
| −10% | ₹11,400 | 4.56% | **PASS**, ₹600 remaining |
| −15% | ₹17,100 | 6.84% | exceeds by ₹5,100 |
| −20% | ₹22,800 | 9.12% | exceeds by ₹10,800 |

(`research.decision.stress.scenario_table()`; tested exactly in `test_scenario_table_and_verdict`.)

**Verdict:** "Adjusted A1 satisfies the −10% scenario budget before costs. The −15% and −20% scenarios exceed that budget. ₹12,000 is not a guaranteed maximum loss, and band flexing does not guarantee an exit."

### Propagation update (25 Sep 2026, later commit)

Adjusted A1 now also applies in:
- **`research/backtest/engine.py` (`EngineConfig`):**
  - defaults are taken from `sizing.py`;
  - new `aggregate_exposure_cap_rs` check, with disposition `REJECTED_GOVERNOR_AGGREGATE_EXPOSURE_CAP`;
  - filled trades count at fill notional and not-yet-filled entries at their reservation, using a strict time comparison;
  - the slot cap is applied at the worst admissible entry price (entry_ref × (1 + clamp) + entry ticks), so a fill never exceeds ₹38,000 and three positions fit under ₹1,14,000. Without this, 1-tick slippage would make the third slot almost always fail the aggregate cap.
- **`research/studies/signal_sim.size_qty`:** a new `notional_px` argument.
- **`research/studies/prereg/resid_rev_v1.yaml`:** the `sizing` block, amended while DRAFT.
- **`research/backtest/metrics.py`:** defaults are now slot cap ₹38,000 and round trip ₹40.40.
- **`research/derivatives/pairs.py`:** default slot cap ₹38,000.

The P2 audit replays pin the pre-A1 sizing explicitly (`slot_cap_rs = 58333.33`, `slot_cap_on_worst_entry = False`). The tables below list the state **before** this update; after it, only the Monte Carlo audit reproduction (historical) and production `antigravity/` still use ₹58,333.

### Consumers still using older settings, as of the Adjusted A1 commit b7e3db6

Adjusted A1 is applied only inside `research/decision/` (sizing, `allocate`/`ExposureBook`, stress). These still use ₹58,333.33 (or ₹58,333):

**Research, outside P6:**

| Location | Setting | Effect |
|---|---|---|
| `research/backtest/engine.py:54` | `EngineConfig.slot_cap_rs = 58333.33` | The engine does its own sizing (`signal_sim.size_qty`) and its own slot/sector/cluster checks. It does **not** call `allocate()`, has **no aggregate cap** and **no pending-order reservation** model. Every backtest, including the P2 regressions and `run_descriptive`, still sizes at ₹58,333.33. |
| `research/studies/prereg/resid_rev_v1.yaml:61` | `sizing: {slot_cap_rs: 58333.33}` | DRAFT pre-registration. It must be changed (by Yashu's decision) before the P7.3 lock, or P7 will study the old sizing. |
| `research/backtest/metrics.py:265, 280` | `slot_cap_binding_stop` and `friction_in_r` defaults | Diagnostics |
| `research/derivatives/pairs.py:141` | `slot_cap_rs = 58333.33` | Pairs sizing (research) |
| `research/execution_realism/monte_carlo_ensemble_audit.py:63` | `SLOT_NOTIONAL_RS = 58_333.0` | 24 Sep audit reproduction; historical, leave |

**Production (`antigravity/`, Rule 8; not touched):**
- `execution_policy.py:124`
- `liquid_momentum_screener.py:348`
- `track2_compass_strategy.py:81`
- `track2_last_light_strategy.py:74`
- `track2_recoil_strategy.py:79`
- `track2_trapdoor_strategy.py:70`
- `track2_volatility_squeeze_strategy.py:92`
- `track2_vwap_reclaim_strategy.py:97`
- `track2_portfolio_risk_governor.py:156` (docstring: ₹1,75,000 deployable / ₹58,333.33 per slot)

The paper desk therefore still sizes at ₹58,333, with no aggregate cap on pending orders.

### Adjusted A1: verification (exact commands, exit codes, results)

All commands were run from `C:\Users\yashw\swing-trades-track2` with `C:\Users\yashw\swing trades\.venv\Scripts\python.exe`.

| # | Command | Code under test | Exit | Result |
|---|---|---|---|---|
| 1 | `python -m pytest research/tests/test_adjusted_a1.py -q -p no:cacheprovider` (Codex's original 10 tests only) | b7dce15, before this change, in a temporary worktree | **1** | `10 failed in 0.51s` |
| 2 | same command, Codex's 10 tests unmodified | this change | **0** | `10 passed in 0.22s` |
| 3 | `python -m pytest research/tests/test_adjusted_a1.py -q -p no:cacheprovider` (Codex's 10 plus 32 added) | this change | **0** | `42 passed in 0.59s` |
| 4 | `python -m pytest research/tests/test_p6_decision.py -q -p no:cacheprovider` | this change | **0** | `32 passed in 9.10s` |
| 5 | `python -m pytest research/tests -q -p no:cacheprovider` | this change | **0** | `509 passed in 41.72s` |

**About the tests:**
- **Codex's file:** Codex added the first 10 tests before the work stopped. The pytest cache showed all 10 failing at 19:48. They are kept unmodified.
- **What Claude added:**
  - the constants;
  - that the risk budget still binds;
  - invalid inputs to `base_qty`, `allocate` and the book;
  - partial-fill transfer and the no-early-release rule;
  - that a partial fill counts as one slot;
  - absolute long/short exposure;
  - the breach on a worse fill;
  - the scenario table and the verdict.
- **Change to the P6 tests:** `test_p6_decision.py` was updated to the new cap. The default test candidate went from ₹50,000 to ₹35,000, the slot-cap and no-cash cases were changed, `base_qty` 116 became 76, and the stress keys were renamed.
- **What a pass does NOT prove:** that the engine, the pre-registration or production use Adjusted A1. They do not (see the list above).

### Superseded (for the record)

Under the plan's ₹58,333.33 cap, the −10% all-positions scenario lost ₹17,500 (7.0% of the corpus), above the ₹12,000 budget. That finding led to Adjusted A1.

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
