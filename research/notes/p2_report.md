# P2 report: engine defects D1–D19

**Date:** 2026-09-25
**Branch:** `track2/decision-engine`, based on cdc0665
**Author:** Claude (red team), implementing plan P2 on Yashu's instruction

**Scope:** research code only (`research/`). Nothing under `antigravity/` was edited.

**Rule 8:** these are core research-engine changes. They need Antigravity's and Codex's review before merging to `main`. Nothing has been pushed.

## 1. Result

| Check | Result |
|---|---|
| Full research suite | **347 passed**, exit 0 (`python -m pytest research/tests -q -p no:cacheprovider`, 23.06 s) |
| P0 baseline at cdc0665 | 314 passed |
| New P1 tests | 4 (evidence hygiene) |
| New P2 tests | 27 defect tests and 2 regression tests |
| Old tests | all still pass, unchanged |
| Regression 1 (audit ORB first breaks) | matches, inside the plan's tolerances (details in section 4) |
| Regression 2 (audit production-code ORB) | n = 41, mean −0.0026R; the audit had n = 41 and −0.003R |
| Runtime, D12 benchmark | 0.136 → 0.017 s per session, about 8× faster; see section 5 for what this does and does not measure |

## 2. How "fails before" was shown, and its limit

Plan rule: for each defect, write a test that fails before the fix and passes after it.

What I actually showed:
- I ran both new test files against a clean clone at cdc0665.
- Both fail **at collection**, with `ModuleNotFoundError: research.studies`, because `signal_sim.py` did not exist yet.

**This is coarse evidence.** It shows the tests cannot pass on the old code. It does **not** show that each individual assertion fails for the specific defect it targets.

Where a test did not depend on the new modules, I checked by reasoning against the old engine code rather than by running it. That covers:
- D2: the old `run_study` silently accepted `var_elm_rate=None`.
- D5: the old code tested the substring "NIFTY".
- D14: the old code produced `int(1 × 0.5) = 0` tranches.

A stricter per-assertion before/after run is possible: stub the missing imports and run each test against old code. I did not do it. If the Rule 8 reviewers want it, it is roughly an hour of work.

## 3. Defects, fixes and tests

All tests below are in `research/tests/test_p2_engine_defects.py` unless marked otherwise.

| # | Defect | Fix | Test(s) |
|---|---|---|---|
| D1 | The engine is long-only everywhere | Every price, R, gap, stop and target path uses side sign sg (`side_sign`). Shorts are opt-in through `EngineConfig.allow_shorts=False`. A short intent with shorts off gets disposition `INVERTED_STOP`, as does any wrong-side stop. | `test_d1_long_and_short_intrabar_stop_prices_and_r`, `test_d1_fees_follow_the_legs`, `test_d1_gap_through_limit_each_side`, `test_d1_stop_wins_when_bar_touches_stop_and_target_each_side`, `test_d1_engine_allocates_shorts_only_when_allowed` |
| D2 | A silent default rejects everything | `run_study` raises `ValueError` when `var_elm_rate is None`. The engine records `MISSING_MARGIN_RATE` instead of dropping silently. | `test_d2_study_requires_margin_rate` |
| D3 | R basis and stop fills | SL-limit model: limit = stop × (1 − sg × offset), rounded away from entry. An open beyond the limit gives `GAP_THROUGH_LIMIT`, which escalates and exits at open − sg × k ticks. An intrabar stop fills at trigger − sg × k ticks. `r_basis` is `trigger` or `stop_limit`; `stop_limit` requires an offset. | `test_d3_stop_limit_basis_and_k_sensitivity`, `test_d3_gap_between_trigger_and_limit_fills_no_worse_than_limit` |
| D4 | Breakeven not checked in the T1 bar | After a T1 fill, the rest of that bar is checked against the breakeven stop, pessimistically: if the bar's low reaches breakeven, the stop fills. | `test_d4_d18_breakeven_checked_in_the_t1_bar_at_the_fill_price` |
| D5 | Indices treated as stocks | New `research/data/indices.py` registry with canonical `IDX:` names and aliases. `is_index_symbol` delegates to it; there is no substring match, so NIFTYBEES is tradable and INDIA VIX is an index. `CandleStore.kind()` returns `INDEX` or `TRADABLE`. The engine never evaluates INDEX series. | `test_d5_index_registry_and_no_trades_on_indices` |
| D6 | Arbitrary allocation order | New `research/decision/allocator.py`. The engine collects every intent at a bar close, then ranks by (strategy priority, −priority_score, sha256(seed \| symbol \| session)). | `test_d6_allocation_ignores_alphabetical_order` |
| D7 | No correlation-cluster cap | `clusters` mapping plus `EngineConfig.max_per_cluster=1`. The disposition is `CLUSTER_LIMIT`. | `test_d7_cluster_cap` |
| D8 | Evidence labels | Allocated trades carry `evidence_class="E1"`; counterfactuals carry `E1_CF`. The existing gate evaluator rejects both, and the test confirms the gate cannot pass on engine output. | `test_d8_evidence_labels_and_gate_never_passes` |
| D9 | Counterfactual quantity fallback | With qty = 0, the simulation runs per share (q = 1), sets `fee_estimated=True`, and returns NaN R when R is undefined (zero risk). | `test_d9_zero_qty_counterfactual_is_per_share_and_nan_when_undefined` |
| D10 | Session shape by instrument class | The policy exit is chosen by clock: the bar that starts at or contains 15:05 exits at its open. This works with 24 and 25 bars. `rms_exits` is counted and is 0 on clean data. **Calibration on bars 1..23 is deferred to P4**, because no calibration code exists yet. | `test_d10_policy_exit_in_both_session_shapes[24]`, `[25]` |
| D11 | Per-strategy series | `per_strategy_daily_pnl` (₹) and `per_strategy_daily_cf_r` (sum of per-signal simulation R, not slot-limited), with zeros on inactive sessions. | `test_d11_per_strategy_series_have_zeros_on_inactive_sessions` |
| D12 | Performance | Per session, precomputed: start → index dicts, bar-end lists (bisect), a daily-bar cache, sector member lists and index series. `universe.check` runs once per (symbol, session). The old per-bar rescans are gone. | Benchmark in section 5 (no pass/fail test) |
| D13 | Richer context | `StrategyContext` gains `index_bars`, `calibration`, `events` and `bar_index`. `SignalIntent` gains `diagnostics`, `priority_score`, `r_basis`, `qty_planned` and `trade_id`. All have defaults. | `test_d13_context_and_intent_fields` |
| D14 | Crash when qty = 1 | `plan_tranches` skips zero-quantity tranches and gives the remainder to the last one. | `test_d14_small_quantities_do_not_crash[1/2/3]`, `test_d14_engine_survives_qty_one` |
| D15 | Counterfactuals don't follow the same rules | One pure function, `research/studies/signal_sim.py::simulate_signal`, serves both the allocated trade and the counterfactual, including the entry clamp (`MISSED_CLAMP`). | `test_d15_counterfactual_matches_allocated_trade_and_respects_clamp`; both regression tests also assert that the engine counterfactual equals the pure function for every signal |
| D16 | Bookkeeping | Checks run in a fixed order, and `ALLOCATED` is set last. New dispositions: `ZERO_QTY`, `NO_NEXT_BAR` and `BLOCKED_PENDING`, so the same symbol cannot be taken twice in one bar. | `test_d16_same_symbol_same_bar_and_missing_next_bar` |
| D17 | `ctx.store` is a look-ahead hole | `ctx.store` is now a `PointInTimeView`. It raises `LookAheadError` on any session on or after the current one; intraday bars come only through `ctx.bars`, which stops at t. | `test_d17_store_view_refuses_current_and_future_sessions`, `test_d17_changing_later_bars_does_not_change_earlier_decisions` |
| D18 | Breakeven reference | Breakeven moves to the actual entry fill, not `entry_ref`. `be_reference` records which one was used. | Same test as D4 |
| D19 | Audit-compatible modes | `entry_mode="signal_close"` and `cost_mode="flat_pct_of_entry_notional"`, with `flat_cost_pct`. Defaults stay `next_open` and the Dhan fee engine. | `test_d19_signal_close_entry_and_flat_cost_mode` |

**Full check order in the engine** (`_handle`):
1. `MISSING_MARGIN_RATE`
2. `INVERTED_STOP`
3. `BLOCKED_PENDING`
4. `BLOCKED_REENTRY`
5. `SHADOW_NOT_ALLOCATED`
6. `MAX_SLOTS`
7. `SECTOR_LIMIT`
8. `CLUSTER_LIMIT`
9. `ZERO_QTY`
10. Simulation outcome (`MISSED_CLAMP` / `NO_NEXT_BAR` / `NO_LIQUIDITY` / `INVALID_RISK`)
11. `ALLOCATED`

A counterfactual is computed for every intent, whatever its disposition.

**Fees under the Dhan cost mode** are charged separately for:
- the entry order;
- each exit order;
- any RMS square-off (the ₹23.60 fee).

## 4. Regressions (`research/tests/test_p2_regressions.py`)

Both runs use:
- data: `shared/track2_liquid/historical_candles_track2.json`;
- `entry_mode="signal_close"`, k = 0, trigger-basis R, flat cost 0.106%;
- two tranches at 1.5R/3R with breakeven, flat at the open of the 15:00 bar.

### Regression 1: first ORB breaks

| Metric | Audit | New engine | Plan tolerance |
|---|---|---|---|
| Events | 103 | 103 | exact |
| Stops | 18 | 18 | ±1 |
| T1 | 10 | 9 | ±1 |
| Time exits | 75 | 76 | ±1 |
| Mean net R | −0.084 | −0.0839 | ±0.005 |
| Mean gross R | −0.003 | −0.0037 | ±0.005 |
| SD net R (F6) | 0.741 | 0.740 | — |

Also checked: the fraction of trades whose size is set by the slot cap, and their median rupee risk, both match F4.

**Why T1 is 9, not 10:**
- The new engine fills a target only on a trade-through: the price must go strictly beyond the target, per the plan's fill rule.
- The audit counted a touch as a fill.
- Exactly one event touched T1 without trading through: **IREDA on 2026-09-18, T1 ₹112.06**. It becomes a time exit instead.
- I verified this by rescanning all 103 events; the script is in the scratchpad and is not committed.
- This is a deliberate rule difference, not a loosened test. It sits inside the plan's own ±1 tolerance.

### Regression 2: audit production-code ORB

This run imports `MultiTimeframeAlphaEngine.evaluate_15m_orb` read-only. Setup: BULLISH regime, 2.5× volume multiple, and trend gates off.

**Result:** n = 41, mean −0.0026R (gross +0.0623, fee 0.0649). The audit had n = 41 and −0.003R.

**Not done here:** the live path, `track2_orb_signal_adapter` → `LiquidMomentumEngine.evaluate_15m_orb_breakout` (ORB_PROD), is P5.1 work. It is not built yet.

## 5. Runtime (D12)

**How it was measured** (scratch script, not committed):
- 180 synthetic symbols, 24 bars per session, 250 prior daily bars each;
- a `NoOp` adapter;
- run with the same Python on both trees;
- old engine: a clean clone at cdc0665; new engine: this worktree.

| Engine | Sessions | Wall time | s/session | Projected 1,200 sessions |
|---|---|---|---|---|
| Old (cdc0665) | 5 | 0.68 s | 0.136 | about 2.7 min |
| New | 5 | 0.08 s | 0.017 | about 0.3 min |
| New | 20 | 0.33 s | 0.017 | about 0.3 min |

**Caveat:**
- This measures engine loop overhead only, with an adapter that does nothing.
- Real adapters, per-intent counterfactuals, fee computation and a 5-year data load will dominate.
- The plan's target is 30 minutes or less for one strategy × 180 symbols × 1,200 sessions. That is **not yet demonstrated on real data**. It has to be re-measured in P5, once real history and a real adapter exist.
- The old engine's worst case, `daily_before` scanning every daily bar on every bar, grows with history length. That growth is why a 250-bar synthetic history understates the old engine's real cost.

## 6. Deviations and contradictions with the plan

1. **Production fee rate.**
   - The plan (section 3) describes production fees as not yet harmonized.
   - Commit 9e712ed had already set the production transaction rate to 0.0030699%, which gives a ₹61.99 round trip on ₹58,333.
   - Research uses the same `DhanFeeEngine`, so the two agree.
2. **pyarrow is not installed** in `.venv`.
   - The P3 Parquet store needs it.
   - Installing it changes the environment, so **it needs Yashu's approval before P3**.
3. **The global `*.lock` gitignore rule** would have hidden the P7.3 pre-registration lock. I un-ignored `research/studies/prereg/*.lock` in commit 1aa0e31.
4. **Worktree and main checkout state.**
   - The worktree already existed (created 12:46 IST, clean).
   - The main checkout had no uncommitted work.
   - The plan expected many uncommitted files there; the only one was the plan file itself.
5. **A Dhan fetcher already exists** (cdc0665: `research/data/dhan_historical_fetcher.py`, `scripts/download_dhan_historical.py`). P3 must review and reuse it rather than write a second one; it has not been reviewed yet.
6. **The SL-M default is kept** (`stop_limit_offset_pct=None`).
   - An existing, pre-P2 test expects SL-M gap behaviour.
   - The plan also says defaults must preserve current behaviour.
   - So the SL-limit model is opt-in, and **every research study config must set `stop_limit_offset_pct=0.005` explicitly**. P5 configs must do this; otherwise they silently run SL-M.
7. **D10 calibration on bars 1..23 is deferred to P4.** No calibration code exists in P2 to apply it to.
8. **Equal-score ties in D6.**
   - The rename test uses distinct `priority_score`s.
   - With equal scores, the tie-break is a seeded hash of the symbol name, so renaming a symbol can change the order.
   - That is intended: the order is deterministic and not alphabetical. But it is **not** rename-invariant.
9. **D12 has no automated test.** It is a benchmark only. The 30-minute target is unverified on real data; see section 5.
10. **"Fails before" was shown only at module level** (see section 2).

## 7. Files

**New:**
- `research/data/indices.py`
- `research/decision/allocator.py`
- `research/studies/signal_sim.py`
- `research/tests/test_p2_engine_defects.py`
- `research/tests/test_p2_regressions.py`
- `research/notes/p2_report.md`

**Modified:**
- `research/backtest/engine.py` (rewritten; the public constructor and `EngineResult` fields are backward compatible)
- `research/backtest/strategies.py`
- `research/backtest/bars.py`
- `research/backtest/study.py`
- `research/PLAN_STATUS.md`

**Not touched:** `antigravity/`, `shared/` data, credentials.

## 8. Next

**STOP**, per the plan. Before P3, I need two things from Yashu:
1. "continue";
2. a decision on installing pyarrow.

Separately, Rule 8 review of this branch by Antigravity and Codex is needed before anything merges to `main`.
