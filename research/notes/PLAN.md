# Track 2 execution plan: decision engine, RESID_REV and a 5-year backtest

**For:** Claude Code, working on the repo at `C:\Users\yashw\swing trades`
**From:** Claude (cloud research session), 25 September 2026. Version 2: an independent review was run against the repo code, and every finding it made was fixed.
**Scope:** Track 2 only (Rule 11). Paper and research only (Rule 1). Nothing in this plan authorises trading with real money.

**Labels.** Every number in this document carries one of four labels. Keep the same labels in everything you write.
- **MEASURED**: from a file in the repo, or a re-run on 25 Sep 2026.
- **DERIVED**: a closed form, checked numerically.
- **LITERATURE**: a published study.
- **ASSUMPTION**: a stated choice.

---

## Kickoff prompt (Yashu pastes this into Claude Code)

> Read `Claude outputs/2026-09-25_track2_execution_plan_for_claude_code.md` in full. Then read AGENTS.md and the files listed in section 3.1 of the plan. Execute phases P0, P1 and P2 in order, following every rule in section 1. Stop after P2, write `research/notes/p2_report.md`, and summarise what you changed, the test results, and anything in the repo that contradicts the plan.

Then, one at a time:
1. "Continue with P3."
2. "Continue with P4–P6."
3. "Continue with P7 up to the pre-registration freeze."
4. "Run the P7 holdout."
5. "Start P8."

---

## How to use this document

1. Execute the phases in order. Each phase has a goal, tasks, files, specifications, tests and acceptance criteria. A phase is done only when its acceptance criteria pass.
2. Phases marked **STOP** end with a report in `research/notes/`. Summarise it to Yashu and wait for an explicit "continue".
3. Keep `research/PLAN_STATUS.md` up to date, one row per task: date, phase, task, status, commit, notes.
4. The code facts in section 2 were checked at commit `3bc73a4` (branch `claude/institutional-backtest-derivatives`). Other agents commit to this repo, so re-check a fact before you build on it. If it has changed, note it in `PLAN_STATUS.md` and adapt.
5. If the plan is wrong about the code or the data, don't force it. Record the difference, pick the conservative option, and raise it at the next STOP.

---

## 0. Goal

**The problem.** Every trading decision on the desk today comes from hand-set numbers:
- volume ≥ 2.5×, ER8 < 0.35, 1.4 × ATR;
- fixed 1.5R/3R targets;
- a conviction score with hand-picked weights (0.30 × volume + 0.25 × relative strength + 0.15 × OFI, in `antigravity/models/track2_multi_strategy_engine.py`).

None of these was estimated from trade outcomes or tested against chance. The only replay (8 stocks, 32 sessions) puts all seven strategies at zero or negative expectancy (MEASURED, section 3.2).

**What this plan builds.** Decisions made from measured, cost-adjusted, statistically tested numbers:
1. Five years of Indian 15-minute data for the Track 2 universe, the sector indices and India VIX, plus a point-in-time history of news and events.
2. A hardened backtest engine: long and short, no look-ahead, fast enough for 180 symbols × about 1,200 sessions.
3. A production-equivalent ORB baseline (`ORB_PROD`) and one pre-registered lead strategy, `RESID_REV` (a no-news sector-residual reversal).
4. A decision engine that:
   - estimates each strategy's net R from a counterfactual ledger;
   - sizes positions by volatility;
   - allocates slots by expected rupee P&L, with correlation-cluster caps;
   - changes a strategy's status only through pre-registered statistical tests.
5. A shadow runner, built only after the evidence supports it and after Rule 8 review.

**The governing rule.** A strategy's status changes only when `research/decision/promotion.py` computes it from recorded evidence. Nothing is "Tier 1" because a document says so.

**Non-goals:**
- live trading;
- edits to `antigravity/` before P8, or without Rule 8 review;
- strategies that are not pre-registered;
- any tuning on the holdout.

---

## 1. Ground rules (binding)

### 1.1 From AGENTS.md (read the whole file)

- **Rule 1:** paper only.
- **Rule 8:** cross-agent review before core-model changes. You may write research code under `research/`. Production changes go to Codex for review and to Antigravity for integration.
- **Rule 11:** Track 2 isolation. Track 2 storage lives in `shared/track2_liquid/`. Never touch Track 1 data or logic.
- **Security section:**
  - no permission-bypass flags;
  - no browser remote-debugging ports (9333, 9444);
  - no browser credential scraping;
  - never delete the repo, `.git`, checkpoints or other agents' work;
  - no destructive git (`reset --hard`, `clean -fd`, force push);
  - report the edits and tests you actually made.

### 1.2 Plan-specific rules

1. **Label numbers.**
   - Every number in code comments, docstrings, docs or reports gets a label and a citation: MEASURED cites a file or test, DERIVED cites an Appendix A equation, LITERATURE cites a paper, ASSUMPTION gives the reason.
   - Every R value also names its basis: `trigger` (risk to the stop trigger) or `stop_limit` (risk to the SL-limit price).
   - Never state a win rate, Sharpe, correlation, drawdown or DSR for a strategy that has no trades.
2. **No look-ahead.**
   - Calibration for session *d* uses only sessions strictly before *d*.
   - A decision at the close of bar *t* uses only bars up to *t*.
   - This applies to adapters as well as features. P2 (D17) and P4 test both.
3. **A touch is not a fill.**
   - Resting limits fill only on a trade-through, meaning price strictly beyond the limit.
   - Stops are SL-limit orders, modelled as in D3: an intrabar stop fills at the trigger minus slippage.
   - If price opens beyond the stop-limit price, the order does not fill (`GAP_THROUGH_LIMIT`). The desk escalates to a marketable exit, modelled at the open minus sg × k ticks and counted separately.
4. **Evidence classes.**
   - Bar-model fills are E1. Counterfactual simulations are E1_CF.
   - Neither is ever admissible for the locked gate, which needs E2/E3.
   - Backtests decide what earns shadow time. They never qualify a strategy.
5. **Secrets.**
   - Dhan credentials are in `antigravity/config/dhan_config.json`. The file is gitignored; its keys are `client_id` and `access_token`.
   - Load them with `load_dhan_config()` from `antigravity/daemons/dhan_feed_bridge.py`, a read-only import.
   - Never print, log, copy or commit them, and mask them in error messages.
   - Hash only the URL and request body, never the headers.
6. **Data is never committed.**
   - Every generated data or output file lives under `TRACK2_HISTORY_DIR`, default `C:\Users\yashw\swing trades\shared\track2_liquid\history\` in the main checkout, or under `research/outputs/`. Both are gitignored before first use (P0).
   - This covers raw downloads, parquet, SQLite ledgers, caches, the universe and sector files, event stores and study outputs.
   - Small test fixtures (≤ 200 KB, no secrets) may be committed under `research/tests/fixtures/`.
7. **Git: work in a separate worktree.**
   - Other agents work in the main checkout. `git switch` there would move their commits onto your branch, and `git add` on their modified files would stage their edits.
   - So create a worktree in P0 and do all your work in it. Commit per task as `track2-quant(Pn): <what>`.
   - Before staging, check that `git diff HEAD -- <path>` contains only your own changes. Do not push unless Yashu asks.
8. **Determinism.** Every run writes a `manifest.json` containing:
   - the git commit;
   - SHA-256 hashes of the config and the pre-registration;
   - the data manifest hash;
   - seeds;
   - Python and package versions;
   - start and end timestamps.
9. **Fail closed.**
   - If a data source fails, is blocked, or needs a login, stop and report.
   - Never quietly substitute synthetic, scraped or browser-derived data.
   - A missing input produces `DATA_INVALID` or a named block reason, never a default that lets a trade through.
10. **Scope discipline.**
    - Add no strategies, filters or parameters beyond this plan without Yashu's approval.
    - Every variant you evaluate goes into the trials registry (P1), because the Deflated Sharpe Ratio depends on the true number of trials.
11. **Data provenance.**
    - Use only official APIs (Dhan) and public NSE archive files, plus NSE website APIs only if Yashu approves them (section 8, decision 7).
    - The existing 32-session file came from a Kite web-session endpoint (`kite.zerodha.com/oms`, per its `source_url` field). Record that in P0, and never refresh data through that endpoint.

---

## 2. What already exists (checked 25 Sep 2026 at HEAD 3bc73a4)

The branch is `claude/institutional-backtest-derivatives`. After 6ec60a4 there are two more commits (08:19 and 08:24 IST, 25 Sep):
- **9c69601** adds `engine.py`, `strategies.py`, `study.py`, `tear_sheet.py`, `universe.py` and `test_bt_engine.py`.
- **3bc73a4** changes these:
  - `cost_model.py`: CNC stamp duty 0.015% and a ₹14.75 DP charge on CNC sells. **The MIS path is unchanged**, so the ₹61.99 known answer still holds.
  - `metrics.py`: adds `two_tranche_breakeven_hurdle`, `slot_cap_binding_stop` and `friction_in_r`.
  - `strategies.py`: adds four stub adapters and `incubated_slate_adapters()`.
  - One new review document (section 2.3).

The main checkout also has many uncommitted modifications, including `AGENTS.md`. Record `git status` and `git diff --stat` in P0. Do not touch these files.

### 2.1 Research code to build on (do not duplicate it)

**`research/backtest/`**
- **`bars.py`**
  - `Bar` holds symbol, a tz-aware bar **start**, minutes and OHLCV, and validates them.
  - Also provides `DailyBar`, the NSE tick table (0.01 / 0.05 / 0.10 / 0.50 / 1.00 / 5.00), `round_to_tick`, `resample`, and `CandleStore.from_historical_json` (the only loader).
  - `is_index_symbol()` matches the substring "NIFTY", so `INDIA VIX` would be treated as a stock (fix: D5).
  - `CandleStore.symbols` returns sorted names.
- **`cost_model.py`**
  - `DhanFeeEngine`: MIS and CNC; exchange fee 0.0030699%; GST on brokerage + exchange + SEBI fees; ₹23.60 RMS fee.
  - A ₹58,333 round trip costs ₹61.99, which is 0.1063% (MEASURED).
  - Production's `calculate_transaction_costs` uses an exchange fee of 0.00297%, which gives ₹61.85.
  - `calculate_order` raises on a zero-quantity fill (see D14).
- **`policy.py`: `SessionPolicy`**, the single clock.
  - 09:30 entries open; 14:50 freeze; 15:00 cancel pending; 15:05 bounded exit.
  - 15:08 hard escalation; 15:10 Dhan RMS; 15:15 end of continuous trading.
- **`strategies.py`**
  - Defines `StrategyContext` (includes `store`, a look-ahead hole: D17), `SignalIntent` (no diagnostics or priority fields), `Decision` and `StrategyAdapter`.
  - A simplified `OrbAdapter`: any close above the OR high, stop at the OR low, 1.5R/3R targets, needs 20 daily bars.
  - **The other six adapters are stubs that always return `NO_PATTERN`.**
  - The four 3bc73a4 adapters are also stubs that always return `NO_PATTERN`: `PEAD_DRIFT`, `SWEEP_RECLAIM`, `LATE_MOMENTUM` and `CAS_REVERSAL`.
    - **`PEAD_DRIFT` and `CAS_REVERSAL` are CNC, held overnight.** They fall outside the Track 2 MIS mandate (flat by 15:08). Do not build them unless Yashu decides otherwise (section 8, decision 8).
    - `SWEEP_RECLAIM` and `LATE_MOMENTUM` are MIS. They are P9 candidates, and each needs its own pre-registration.
    - `LATE_MOMENTUM` overlaps LAST_LIGHT, which already has 3 rows in the trials registry. Count it as the same family for the DSR N.
- **`engine.py`: `BacktestEngine`**
  - Per-session event loop. Entry at the next bar open + 1 tick, with a 10 bps clamp.
  - Stop is checked before targets; targets need a trade-through; stop moves to breakeven after T1; `max_bars` time stop.
  - Policy exit in the bar that contains 15:05; RMS fallback.
  - Counterfactuals for rejected intents.
  - Defects D1–D19 are listed in P2.
- **`metrics.py`**
  - Provides `sharpe`, `sortino`, `max_drawdown`, `calmar`, `probabilistic_sharpe`, `expected_max_sharpe`, `deflated_sharpe` (per-period SR, raw kurtosis), `clustered_t` (CR1; returns only t), `block_bootstrap_ci` (by session), `probability_of_backtest_overfitting` (CSCV), `markout_bps`, `r_multiple_summary` and `evaluate_gate`.
  - **`evaluate_gate` is the locked gate decided on 25 Sep:** at least 60 sessions, at least 85 E2/E3 executions, and a day-clustered t of at least 2.0. Use these functions; don't re-implement them.
  - **From 3bc73a4:** `two_tranche_breakeven_hurdle(c, q)` = (1 + c) / (1.75 + 1.5q).
    - It matches A.3 (0.6137 / 0.5653 / 0.4296 at c = 0.074). Reuse it.
    - Its docstring's "40% / 50%" are T → ∞ limits. Within the session, most trades time out (A.4).
    - `friction_in_r` defaults to ₹61.86. Always pass the research value, ₹61.99, explicitly.
- **`cross_validation.py`:** `PurgedKFold`, `CombinatorialPurgedKFold`, `cpcv_selection_paths`.
- **`study.py`:** `run_study` writes `summary.json`, `signals.csv`, `trades.csv` and `tear_sheet.html`. Extend it; don't fork it.
- **`universe.py`:** `PointInTimeUniverse`, which does a linear scan per check (see D12).
- **`order_book.py`, `tear_sheet.py`.**

**`research/derivatives/`:** greeks, gex, basis, pairs, vix_regime, mlofi.
- **`vix_regime.VixRegimeFilter` uses placeholder tiers (11.5 / 16.5 / 22) and uncalibrated multipliers, as its own docstring says. Do not use it for sizing.**

**`research/execution_realism/`:** the 4-state fill model, capacity ledger, exits, surveillance gate and the Monte Carlo audit. It is the reference for E2/E3 evidence in P8.

**Tests:** `python -m pytest research/tests -q` from the repo root gave **306 passed** before 3bc73a4 (MEASURED, 25 Sep, on a Linux VM). 3bc73a4 adds tests, so expect a few more. Re-run on Windows in P0, record the count, and treat any failure as a P0 finding.

### 2.2 Data on disk

**`shared/track2_liquid/historical_candles_track2.json`** is the only intraday history in the repo.
- 8 stocks (ANGELONE, BDL, CDSL, COCHINSHIP, INOXWIND, IREDA, RVNL, SUZLON) plus NIFTY50.
- 15-minute bars stamped at bar start, 10 Aug–23 Sep 2026: 32 sessions, all after the closing-auction (CAS) change.
- Stocks have 24 bars a day. **NIFTY has 25** (its last bar starts at 15:15, even after CAS).
- It holds only 32 daily bars. The simplified ORB needs 20 daily bars, and RESID_REV needs 40 or more prior sessions. **This file cannot exercise the new strategies.** Use synthetic data for unit tests. Never lower a `min_valid` just to make tests pass.

**`shared/track2_liquid/dhan_scrip_master.csv`** is Dhan's instrument master. These NSE index security IDs (segment `I`) were seen on 25 Sep. Re-read them from the master; do not hard-code them.

| Index | ID | Index | ID | Index | ID |
|---|---|---|---|---|---|
| NIFTY 50 | 13 | FINNIFTY | 27 | NIFTY REALTY | 34 |
| **INDIA VIX** | **21** | NIFTY FMCG | 28 | NIFTYPSE | 41 |
| NIFTY AUTO | 14 | NIFTYIT | 29 | NIFTY ENERGY | 42 |
| NIFTY PVT BANK | 15 | NIFTY MEDIA | 30 | NIFTYINFRA | 43 |
| BANKNIFTY | 25 | NIFTY METAL | 31 | NIFTYCPSE | 45 |
| NIFTY PHARMA | 32 | NIFTY PSU BANK | 33 | MIDCPNIFTY | 442 |
| NIFTY HEALTHCARE | 447 | NIFTY CONSR DURBL | 466 | NIFTY OIL AND GAS | 470 |
| NIFTY IND DEFENCE | 493 | | | | |

**`.venv`** has dhanhq 2.2.0, numpy and pandas 3.0.6. Check for pyarrow and install it if missing (Appendix D).

### 2.3 Documents that are NOT evidence

- **`shared/track2_liquid/reviews/deep_mathematical_strategy_research_20260925.md`** (commit fbc668b).
  - It gives win rates, Sharpes, correlations, drawdowns and a "DSR of 0.96" for strategies that have zero trades.
  - Its tiers contradict measured results (Appendix C.2).
  - Use no number from it. P1 writes an erratum next to it.
- **`shared/track2_liquid/reviews/indian_equity_alpha_slate_audit_reconciliation_20260925.md`** (commit 3bc73a4).
  - Its cost and slot-cap arithmetic agrees with this plan: ₹61.86–61.99, S\* = 2.571%.
  - Its S1–S4 slate is literature-ranked, not measured.
  - It assumes σ_R ≈ 1.30R. The measured value is 0.74 (section 3.2).
  - Treat it as hypotheses only.
- **Mandate texts** (`shared/track2_liquid/CLAUDE_DEEP_MATHEMATICAL_RESEARCH_MANDATE.md`, `shared/track2_liquid/track2_master_engineered_audit_mandate.md`) are requirements, not evidence.
- **`research/derivatives/vix_regime.py`** multipliers are placeholders.
- **The audit's prose value "−0.046R"** for ORB first breaks. Its own probe prints −0.084R (section 3.2).

---

## 3. Evidence base

### 3.1 Read these first

1. `AGENTS.md`.
2. `Claude outputs/2026-09-24_beacon_mandate_red_team_audit.md` and its probes in `Claude outputs/2026-09-24_beacon_probes/`. `probe_empirical.py` is the reference replay and runs read-only from the repo root.
3. `Claude outputs/2026-09-24_monte_carlo_ensemble_audit.md`.
4. `research/backtest/*.py` and `research/tests/test_bt_*.py`.
5. Appendices A (maths), B (pre-registration) and C (errata and seeds).

### 3.2 Measured facts

All values come from `probe_empirical.py`, re-run on 25 Sep: 8 stocks, 32 sessions, one regime, optimistic entries at the bar close. **All R values here use the trigger basis.** The fact marked ★ is a regression target in P2.

| Fact | Value |
|---|---|
| ★ First ORB breaks: first close above the 09:15 high before 14:45, no volume gate; two-tranche 1.5R/3R; breakeven; flat at the open of the 15:00 bar; cost 0.106% of entry notional, in per-trade R | 103 events. 18 stops (17.5%), 10 reached T1 (9.7%), 75 time exits (72.8%). Mean net R −0.084 = gross −0.003 − cost 0.080. q = P(T2 given T1) = 1/10 |
| First signal per stock-day through the production strategy code (section C2) | ORB −0.003 [−0.20, +0.22], n = 41 · VWAP_RECLAIM −0.258 [−0.45, −0.05], n = 89 · VOL_SQUEEZE −0.530 [−0.79, −0.27], n = 13 · TRAPDOOR −0.077 [−0.35, +0.19], n = 18 · LAST_LIGHT +0.226 [−0.20, +0.73], n = 11 · RECOIL −0.381 [−0.86, +0.09], n = 7 (was n = 5 before commit d62c7e6; SELLs rejected by the governor) · COMPASS −0.095 [−0.27, +0.11], n = 17 |
| Sensitivity sweep (section F) | 21 configurations, listed in C.3. None is significantly positive. VWAP_RECLAIM and VOL_SQUEEZE are negative at every threshold. |
| Stop distance to the OR low | median 1.45%, p10 0.83%, p90 2.25%. The ₹58,333 cap binds on 96.1% of trades, so median realised risk is about ₹832, not ₹1,500. |
| Passive bid at close − 0.25 × bar range | fills 81% of losers and 39% of winners. Win share among fills is 26.2%, against 42.7% for all signals. |
| Outcome dispersion | σ_R = 0.74 (trigger basis) |
| Mean pairwise within-session 15-minute correlation | 0.32 (IREDA–RVNL 0.50; RVNL–SUZLON 0.47; BDL–COCHINSHIP 0.42) |
| Worst 5% of NIFTY bars | 89% of the stocks fall in the same bar |
| Session ATR20 vs typical bar range | 1.75× at 10:45; 1.63× at 12:00 and 14:00 |
| ER8 of a driftless random walk | mean ≈ 0.354; P(ER8 ≥ 0.35) = 0.45 |
| 1-hour σ; 09:15 bar range vs midday | ≈ 0.53% of price; ≈ 1.25% vs ≈ 0.25% |
| Monte Carlo audit | the old rule (60 sessions, 20 fills, positive mean) passes a zero-edge ensemble 48% of the time |

### 3.3 Derived facts (checked numerically on 25 Sep)

| Fact | Value | Eq. |
|---|---|---|
| Zero-drift barrier model vs the ORB replay | By 15:00 it predicts 69.4% time exits, 23.2% stops and 7.3% T1. Measured: 72.8 / 17.5 / 9.7%, with χ² p ≈ 0.23. This is consistent with zero drift. (The 60/40 stop/T1 split applies only in the T → ∞ limit.) | A.4 |
| Exits and edge | With constant drift, E[gross R] = drift × expected time in the trade for any bracket or tranche rule. Exits can add value only by timing a drift that changes with state (A.5). | A.5 |
| False-signal rate when scanning \|Z\| ≥ 2.5 over 15-minute bars (Gaussian, iid) | 8.1–8.2% per stock-day over 23 returns; 6.2–6.3% inside the 10:00–13:30 window; 1.24% for a single look. The 95th percentile of the window maximum is 2.59. | A.8 |
| Power at σ = 0.74, independent trades, one-sided 5%, 80% | n = 1,355 / 339 / 85 / 38 for δ = 0.05 / 0.10 / 0.20 / 0.30R | A.13 |
| Power under the locked t ≥ 2.0 gate, independent trades | 85 executions give 69% power for +0.20R; 80% needs 111 (443 for +0.10R). Day clustering lowers power: with 2 trades per session and within-day correlation 0.3, 80% needs about 144. | A.13 |
| Expected maximum drawdown at zero edge, 250 trades | 14.66R in continuous time; 13.7R as a discrete walk (about ₹11,400 at ₹832 per R); about 25R if net drift is −0.08R per trade | A.16 |
| Pair vs trading the dislocated leg alone | a pair wins only above a threshold ρ* that depends on μ (A.12) | A.12 |

---

## 4. Target architecture (new modules, created in the worktree)

```
research/
  PLAN_STATUS.md                       # progress log (P0)
  notes/                               # phase reports
  evidence/
    MEASURED_FACTS.md                  # single source of measured numbers (file + reproduction command each)
    trials_registry.csv                # every variant ever evaluated (feeds DSR)
  data/                                # P3
    paths.py                           # TRACK2_HISTORY_DIR, main-checkout path, output dirs (all gitignored)
    instruments.py                     # scrip master -> security ids; symbol history (renames)
    indices.py                         # index registry: IDX:NIFTY50, IDX:INDIAVIX, IDX:NIFTYMETAL, ...
    dhan_history.py                    # rate-limited, resumable Dhan historical downloader (CLI)
    nse_events.py                      # announcements, board meetings, corporate actions, F&O ban history (CLI)
    universe_build.py                  # point-in-time Track 2 universe per session
    sector_map.py                      # symbol -> factor index
    store_parquet.py                   # ParquetCandleStore implementing the CandleStore interface + HoldoutGuard
    validate.py                        # data quality + coverage report
  features/                            # P4
    timeprofile.py, calibration.py, events.py, session_cache.py
  strategies/                          # P5
    orb_prod.py, resid_rev.py
  decision/                            # P6
    ledger.py, estimator.py, sizing.py, clusters.py, allocator.py, promotion.py, stress.py
    register.json                      # strategy status (written ONLY by promotion.py)
    records/                           # DecisionRecord JSON
  studies/                             # P7
    prereg/resid_rev_v1.yaml, prereg/resid_rev_v1.lock, prereg/orb_prod_v1.yaml
    run_design.py, run_holdout.py, signal_sim.py
  shadow/run_day.py                    # P8
  tests/fixtures/ ...                  # small committed fixtures
```

**Dependencies.**
- The logic in `features/`, `strategies/` and `decision/` uses only the standard library and numpy.
- pandas and pyarrow are allowed in `data/` and `studies/`.
- List them in `research/requirements-research.txt`, and leave the production `requirements.txt` alone.

---

## 5. Phases

| Phase | Effort | Needs | Ends with |
|---|---|---|---|
| P0 Worktree and baseline | S | none | `p0_baseline.md` |
| P1 Evidence hygiene | S | none | registry, facts file, erratum, register |
| P2 Harden the engine | M–L | 32-session file and synthetic data | regressions pass; `p2_report.md` (**STOP**) |
| P3 Data layer | M, plus download time | Dhan Data API; decision 7 | `p3_data_report.md` (**STOP**) |
| P4 Features and calibration | M | synthetic data, then P3 data | look-ahead tests pass |
| P5 Strategies | M | same | strategy tests pass |
| P6 Decision engine | M | same | decision tests pass |
| P7 Studies | M | P3 data | frozen pre-registration (**STOP**); holdout report (**STOP**) |
| P8 Shadow integration | L | live feed; Rule 8 | review requests (**STOP**) |
| P9 Later strategies | – | after P7 only | – |

**Parallelism.** While the P3 download runs, build P4–P6 against synthetic data and the 32-session file.

### P0: Worktree and baseline

1. **Check the main checkout.** Run `git status` and `git log --oneline -5` and record the output. Do not change anything there.
2. **Create a worktree:** `git worktree add "C:\Users\yashw\swing-trades-track2" -b track2/decision-engine`, from the main checkout's HEAD. Work only inside it.
   - Use the main checkout's `.venv` (`"C:\Users\yashw\swing trades\.venv\Scripts\python.exe"`).
   - Read credentials from the main checkout's `antigravity\config\dhan_config.json`, through `research/data/paths.py` with an env override.
   - Set `TRACK2_HISTORY_DIR` to `C:\Users\yashw\swing trades\shared\track2_liquid\history`.
   - This plan file is untracked in the main checkout, so the worktree won't have it. Copy it to `research/notes/PLAN.md` in the worktree and commit it. From then on, that copy is the one you follow.
3. **Ignore generated data first.** Add `shared/track2_liquid/history/` and `research/outputs/` to `.gitignore` in the worktree, and commit that before generating anything.
4. **Run the tests from the worktree root:** `...python.exe -m pytest research\tests -q`. Record the counts.
5. **Write the baseline notes.** Create `research/PLAN_STATUS.md` and `research/notes/p0_baseline.md`. The baseline note records:
   - the test counts;
   - HEAD;
   - whether each claim in section 2 still holds (yes/no, with evidence);
   - the provenance of the Kite file (its `source_url` field).

**Acceptance:** the worktree exists, the ignore rules are committed, and the baseline note is written.

### P1: Evidence hygiene

1. **`research/evidence/MEASURED_FACTS.md`.** Copy section 3.2 and add the source file and reproduction command for each fact. For example, `python "Claude outputs/2026-09-24_beacon_probes/probe_empirical.py"`, section D.
2. **`research/evidence/trials_registry.csv`.**
   - Columns: `trial_id, date_run, strategy_id, variant, data_span, sample_id, n_trades, mean_net_r, r_basis, sr_per_trade, source, agent, notes`.
   - `sample_id` identifies the data sample, so that the DSR's variance term uses same-sample trials only (A.14).
   - Seed it with the 21 configurations in C.3.
   - From now on, add every variant you evaluate, including discarded ones.
3. **Erratum.** Write `shared/track2_liquid/reviews/claude_erratum_deep_research_20260925.md`, based on C.2.
   - Do not edit the original. Rule 8 means reporting disagreement, not overwriting.
   - This file sits in the shared reviews folder of the worktree; mention it in the P2 report so Antigravity sees it.
4. **`research/decision/register.json`.**
   - Initial state: every strategy is `UNVERIFIED` with `evidence: none`.
   - Add a test that greps the package and asserts that only `research/decision/promotion.py` writes this file.

**Acceptance:** all four files exist, and the single-writer test passes.

### P2: Harden the backtest engine (`research/backtest`)

For each defect, write a test that fails before the fix and passes after it. Keep the public interfaces backward compatible: new parameters get defaults that preserve current behaviour, except where the default itself is the defect (D2). Research study configs set the new options explicitly.

#### D1. The engine is long-only everywhere

**Problem.** `_assess_intent` rejects every non-BUY intent. The clamp, `_process_bar_for_trade`, `_finalize_trade`, `_close_rms_squareoff` and the counterfactual all assume a long position. That includes the POLICY_EXIT, TIME_STOP and RMS exit prices (open or close − tick) and the sign of the `net_r` denominator.

**Fix:** make every step side-aware, with sg = +1 for BUY and −1 for SELL.

| Step | Rule |
|---|---|
| Entry fill | next open + sg × tick |
| Clamp miss | sg × (open − entry_ref) > clamp × entry_ref |
| Stop hit | sg × (adverse extreme − stop) ≤ 0; the adverse extreme is the low for longs and the high for shorts |
| Opens beyond the stop-limit | `GAP_THROUGH_LIMIT`: escalate and exit at open − sg × k ticks (D3) |
| Target fill | trade-through: high > T for longs, low < T for shorts |
| Time, policy and RMS exits | price − sg × tick |
| P&L | sg × Σ (exit − entry) × qty |
| Fees | entry leg BUY for longs, SELL for shorts; exits on the opposite side |
| R denominator | always positive |

- Add `EngineConfig.allow_shorts: bool = False` to keep backward compatibility. Research study configs set it to True. The production governor stays long-only until P8.

**Tests (do not use a naive price reflection).** On hand-built bar series for a long and a short:
- check the exit reason, exit time and gross R per share, with prices inside one tick band;
- check fees separately, since STT applies to the sell leg and stamp duty to the buy leg;
- cover a gap-through on each side;
- when a bar touches both the stop and a target, the stop wins, on each side.

#### D2. A silent default rejects everything

**Problem.** With `EngineConfig.var_elm_rate=None`, every intent is rejected as `REJECTED_GOVERNOR_MISSING_MARGIN_RATE`, so every study becomes counterfactual-only without saying so.

**Fix:**
- `run_study` requires an explicit `var_elm_rate`. For research use 0.20, an ASSUMPTION for MIS margin on F&O stocks; record it in the manifest.
- `summary.json` reports counts per disposition.

**Test:** leaving it unset raises an error; setting it produces allocated trades.

#### D3. R basis and stop fills

**Fix:**
1. `stop_limit` = stop × (1 − sg × 0.005), rounded away from entry. This is production's SL-limit offset.
2. `risk_rs_planned` = qty × |entry_ref − stop_limit| for `r_basis="stop_limit"`, or qty × |entry_ref − stop| for `r_basis="trigger"`.
3. An intrabar stop fills at stop − sg × k ticks. k = 1 by default; the sensitivity runs use 0, 2 and 3.
4. If the bar opens beyond `stop_limit`, record `GAP_THROUGH_LIMIT` with the fill at open − sg × k ticks, and count these events.
5. Store `gross_r`, `fee_r`, `slip_r` and `net_r`, with an explicit `r_basis` field.
   - New studies default to `"stop_limit"`, which matches production sizing.
   - The audit regression uses `"trigger"`.
   - Every report states the basis.

#### D4. The breakeven stop is not checked in the T1 bar

**Problem.** After T1 fills inside a bar, the stop moves but is not checked in that same bar.

**Fix:** if the same bar's adverse extreme crosses the new breakeven stop, close the remainder at breakeven. This is conservative and matches `probe_empirical.simulate`.

#### D5. Index series are treated as stocks

**Problem.** `is_index_symbol` is a substring test for "NIFTY", and the decision loop skips only `"NIFTY50"`.

**Fix:**
- Add an explicit index registry (`research/data/indices.py`) with canonical names such as `IDX:NIFTY50` and `IDX:INDIAVIX`, and keep `NIFTY50` as an alias.
- The store marks each series as INDEX or TRADABLE.
- The engine never evaluates adapters on an INDEX series.

**Test:** a universe that includes `IDX:INDIAVIX` never produces a trade on it.

#### D6. Slot allocation order is arbitrary

**Problem.** Intents at a bar are allocated in iteration order, and `CandleStore.symbols` is alphabetical, so there is an alphabetical bias.

**Fix:**
- Collect all intents at a bar close, then call an `Allocator`. The interface lands now; the implementation comes in P6.
- The default ranking is the strategy priority, then `priority_score`, then a seeded hash of (symbol, session).

**Test:** renaming symbols, which changes alphabetical order but not the data, does not change the allocations.

#### D7. There is no correlation-cluster cap

**Fix:**
- Add an optional per-session `clusters` mapping and a `max_per_cluster` setting (default 1).
- Keep the sector cap at 2.

#### D8. Evidence labels

**Fix:** label bar-model trades `evidence_class="E1"` and counterfactuals `"E1_CF"`.

**Test:** assert the label values, and assert that `metrics.evaluate_gate` never passes on engine output.

#### D9. Counterfactual quantity fallback

**Problem.** When sizing gives 0 shares, the counterfactual falls back to `qty = 100`.

**Fix:**
- Compute counterfactual R per share, with fees at the planned quantity. Flag `fee_estimated` if that quantity is 0.
- **Return NaN, not 0.0, when R is undefined.** Today, SELL counterfactuals silently get `net_r = 0.0`.

#### D10. Session shape depends on the instrument class

**Rule:**
- **F&O stocks (CAS stocks):** 25 bars (09:15–15:30) before 2026-08-03, and 24 bars (09:15–15:15) from that date.
- **Indices:** 25 bars throughout. Verify this on the downloaded data.
- The 15:05 policy exit falls in the 15:00 bar in both shapes.

**Fix:**
- Add an `rms_exits` counter; it must be 0 on clean data.
- Calibration uses only bar slots common to both regimes: returns for bars 1..23.

**Test:** both shapes, and both instrument classes.

#### D11. Per-strategy series

**Fix:** output two series with zeros on inactive sessions:
- `per_strategy_daily_pnl` (₹);
- `per_strategy_daily_cf_r`, the per-session sum of the per-signal simulation R (P7.4), not limited by slots.

#### D12. Performance

**Problem.** The engine re-scans lists for every symbol on every bar:
- `next(...)` over the bars;
- a full comprehension of bars up to t;
- peers rebuilt from all symbols;
- `store.daily_before`, which scans every daily bar for every symbol on every bar (about 7×10⁹ scans over 5 years);
- `universe.check`, which is a linear scan.

**Fix:**
- Precompute, per session: a start → bar dict per symbol, index arrays, and per-sector member lists.
- Cache `daily_before` and `universe.check` per (symbol, session).

**Target:** one strategy over 180 symbols × about 1,200 sessions in 30 minutes or less. Measure it and report it.

#### D13. Richer context for new adapters

Extend `StrategyContext` with these fields (backward compatible):
- `index_bars` (up to the decision time);
- `calibration`, `events` and `bar_index`.

Extend `SignalIntent` with optional fields:
- `diagnostics: Mapping`;
- `priority_score: float`;
- `r_basis`.

#### D14. Crash when qty = 1

**Problem.** For T1, `int(qty × 0.5)` is 0 when qty = 1. That creates a zero-share exit, and `DhanFeeEngine.calculate_order` raises. This hits stocks priced ₹29,167–58,333 (qty = 1 under the slot cap). A single such trade kills a multi-year run.

**Fix:** skip zero-quantity tranches and assign the remainder to the last tranche.

**Test:** qty ∈ {1, 2, 3}.

#### D15. Counterfactuals don't follow the same rules

**Problems.**
- The counterfactual ignores the entry clamp. A gapped entry can post a counterfactual gain even though the allocated path would record `MISSED_CLAMP`.
- `BLOCKED_REENTRY` and `SHADOW_NOT_ALLOCATED` handling differ from the rest.

**Fix:**
- Move all per-signal simulation into one function, `research/studies/signal_sim.py`, which applies identical fill rules to every emitted signal, including the clamp.
- The engine and the counterfactual path both call it.

#### D16. Bookkeeping

**Problems.**
- The disposition is set to `ALLOCATED` before the quantity check.
- A pending order whose next bar is missing is dropped silently and left as `PENDING`.
- Two adapters can enter the same symbol in the same bar, because pending orders are not checked.

**Fix:**
- Add dispositions `ZERO_QTY` and `NO_NEXT_BAR`.
- Check pending symbols before approving an intent.

#### D17. `StrategyContext.store` is a look-ahead hole

**Problem.** A probe adapter can read future bars of the current session through `ctx.store`.

**Fix:** remove `store` from the context, or replace it with a point-in-time view that raises on any bar ending after the decision time or any session on or after the current one.

**Test:** an adapter tries to read the future and gets an exception. A second test mutates bars after t and checks that decisions are unchanged.

#### D18. Breakeven reference

**Problem.** The breakeven stop moves to `entry_ref`, the decision price.

**Fix:** move it to the actual entry fill price, and record which one was used.

#### D19. Audit-compatible engine modes

The regression needs `entry_mode="signal_close"` and `cost_mode="flat_pct_of_entry_notional"`. Add both as options; the defaults stay `next_open` and `DhanFeeEngine`.

#### Regression test 1: the audit's ORB first breaks

- **Configuration:**
  - `entry_mode="signal_close"`;
  - `r_basis="trigger"`;
  - k = 0;
  - two-tranche 1.5R/3R with breakeven, using D4 same-bar logic;
  - flat at the open of the 15:00 bar;
  - `cost_mode` flat at 0.106% of entry notional;
  - first close above the 09:15 high before 14:45, no volume gate.
- **Expected:** 103 events; 18 stops, 10 T1, 75 time exits (±1 each); mean net R −0.084 (±0.005); gross −0.003 (±0.005).
- **If it does not match, find out why. Do not loosen the test.**

#### Regression test 2: the audit's production-code ORB

- **What the audit ran:** section C used `MultiTimeframeAlphaEngine.evaluate_15m_orb` in `track2_alpha_engine.py`, with trend gates disabled and a 2.5× volume multiple. It produced n = 41 with a mean of −0.003R.
- **What to do:** reproduce that result on the same path (n = 41 ± 3, mean inside the audit's CI).
- **Separately:** the desk's live path is `track2_orb_signal_adapter` → `LiquidMomentumEngine.evaluate_15m_orb_breakout`. Build ORB_PROD on the live path (P5.1) and report both paths side by side.

**Acceptance:**
- All old and new tests pass.
- `research/notes/p2_report.md` lists each defect with its fix and test, and gives the runtime before and after.

**STOP.**

### P3: Data layer

**Prerequisites:**
- An active Dhan Data API subscription (₹499 + taxes a month, per Dhan's support page, 25 Sep 2026).
- Decision 7 in section 8 on NSE website APIs.

#### P3.1 `research/data/paths.py`

Resolve these paths from environment variables, with defaults:
- `TRACK2_HISTORY_DIR`;
- the main-checkout path, used for credentials;
- `research/outputs/`.

Every writer in this plan uses these paths.

#### P3.2 `research/data/instruments.py`

- **Stocks:** exchange NSE, series EQ, segment `NSE_EQ`, instrument `EQUITY`.
- **Indices:** segment `IDX_I`, instrument `INDEX`.
- **Symbol history:** TATAMOTORS became TMPV after the October 2025 demerger (record date 14-Oct-2025); TMCV listed on 12-Nov-2025.
- **Key by security ID**, not symbol.
- **Fixture:** commit a small extracted fixture.

#### P3.3 `research/data/dhan_history.py` (CLI)

- **Contract.** Verified on 25 Sep from https://dhanhq.co/docs/v2/historical-data/. Cross-check the field names against the installed dhanhq 2.2.0 SDK (`intraday_minute_data`) and against one recorded live response, kept as a fixture.
  - Request: `POST https://api.dhan.co/v2/charts/intraday`. Headers: `access-token`, `client-id`, `Content-Type: application/json`, `Accept: application/json`.
  - Body fields:
    - `securityId` (string);
    - `exchangeSegment`: `NSE_EQ` or `IDX_I`;
    - `instrument`: `EQUITY` or `INDEX`;
    - `interval` = 15 (the docs allow 1, 5, 15, 25 and 60; the SDK shows whether it is sent as a string or an integer);
    - `oi` = false;
    - `fromDate` and `toDate` as `"YYYY-MM-DD HH:MM:SS"`.
  - Response arrays: `open`, `high`, `low`, `close`, `volume`, `timestamp` (epoch seconds) and `open_interest`.
  - Intraday history goes back 5 years. **A single request can span at most 90 days.**
- **Daily bars:** `POST /v2/charts/historical`, dates as `"YYYY-MM-DD"`, with `toDate` exclusive.
- **Windows:** 85 days each, from 2021-10-01 (or the earliest date Dhan serves) to the latest completed session.
- **Rate limits:**
  - token bucket at 3 requests per second; Dhan allows 5/s for data APIs, and the headroom keeps the live feed safe;
  - backoff on 429 and 5xx, up to 5 retries;
  - count requests against the 100,000-per-day limit;
  - expect about 4,500 requests in total.
- **Resume manifest:** one line per (instrument, window) with the request hash (URL + body only), status, bytes, SHA-256 of the body, rows and `fetched_at`.
- **Storage:** raw gzip files under `<TRACK2_HISTORY_DIR>/raw/dhan/<secid>/`, then parsed into parquet under `<TRACK2_HISTORY_DIR>/bars_15m/` and `.../daily/`.
- **Timestamps:** epoch → UTC → IST.

#### P3.4 `research/data/nse_events.py` (CLI)

**Sources.** Prefer public NSE archive files on `nsearchives.nseindia.com`, for example the F&O ban lists `/archives/fo/sec_ban/fo_secban_DDMMYYYY.csv`.

**NSE website JSON APIs require Yashu's approval (section 8, decision 7) before any use.** These are:
- `https://www.nseindia.com/api/corporate-announcements?index=equities&from_date=DD-MM-YYYY&to_date=DD-MM-YYYY`
- `.../api/corporate-board-meetings?...`
- `.../api/corporates-corporateActions?...`

If approved:
- use `requests.Session` with a plain User-Agent that identifies the client honestly;
- make at most one request every 2 seconds;
- keep a resume manifest;
- **never read cookies or tokens from any browser profile.**

On repeated 401 or 403 responses, **STOP** and report.

**Point-in-time keys:**
- **Announcements:** the exchange dissemination timestamp (IST). Verify the field name from a recorded response.
- **Scheduled events** (results and board meetings): the **intimation** timestamp, i.e. when the market was told the meeting would happen, not the meeting date. A decision on day d may use only events intimated before the decision time.
- **Ex-dates:** from corporate actions, keyed by the announcement timestamp.

**Fallback.** If the announcements history can't be obtained or isn't approved, RESID_REV runs only as the registered variant `RESID_REV_NF` (no news filter), and every report says so.

#### P3.5 `research/data/universe_build.py`

**Eligibility per session:**
- F&O member on that date;
- previous close ≥ ₹10;
- DTV20 (median turnover over the prior 20 sessions, from daily bars) ≥ ₹30 Cr;
- not in that day's F&O ban;
- not under ASM/GSM, if that history is available (otherwise flag `SURVEILLANCE_HISTORY_MISSING`).

**Market-cap band (₹4,000–75,000 Cr):**
- Apply it historically only as price_t × lagged shares outstanding, if a shares history is available.
- Otherwise do **not** apply it to historical sessions; applying today's market cap to past sessions is look-ahead. Label the universe `MCAP_BAND_NOT_APPLIED`.

**F&O membership history:**
- Prefer NSE's inclusion and exclusion notices.
- The fallback is the current list plus named exits. Quantify the survivorship bias.

**Output:** `universe_daily.parquet` (session, symbol, eligible, reason), which feeds `PointInTimeUniverse`.

#### P3.6 `research/data/sector_map.py`

- **Factor index for each symbol:**
  - Primary: NSE sectoral index membership, choosing the most specific index by a fixed priority list.
  - Fallback: NIFTY 50.
- **Constituent lists are current**, which is a look-ahead bias. If a static industry classification is available, prefer it for the mapping and state the choice.
- **Index history:** if a sectoral index has no history for a date, use NIFTY 50 on that date and record it.
- **Stock weight:** if the stock's weight in its sectoral index is above 10% (where weights are available), use NIFTY 50. The index contains the stock itself.
- **Output:** `sector_map.csv` with columns symbol, factor_index, source, as_of, weight_if_known.

#### P3.7 `research/data/store_parquet.py`

- `ParquetCandleStore` implements the `CandleStore` interface.
- Loading is lazy per session, with a rolling 60-session cache.
- Index series use canonical names.

**`HoldoutGuard` (see P7.1 for the dates):**
1. If `research/studies/prereg/<id>.lock` is absent, or its hash and commit don't match the YAML, the store refuses to serve any holdout session for strategy evaluation.
2. `mode="QA"` serves any session for validation only. It never returns data to strategy code (checked by a test), and every QA read is logged.
3. The legacy JSON loader also goes through the guard.

#### P3.8 `research/data/validate.py`

Checks per symbol-session:
- **Bar counts** by instrument class (D10). Exclude and list any other count, such as special sessions.
- **Bar starts:** first bar at 09:15, then contiguous 15-minute starts.
- **OHLC** consistency.
- **Volume:** a non-negative integer.
- **Duplicates.**
- **Flags:** zero-volume bars, and bar returns above 20%.
- **Daily vs intraday:** high and low within one tick of the daily bar; the distribution of intraday-volume-sum ÷ daily volume.

#### P3.9 Cross-source proof

For the 8 symbols × 32 sessions in the Kite file (QA mode), compare Dhan bars with Kite bars:
- expect identical bar starts;
- OHLC within 1 tick;
- volume within 1%;
- report every mismatch.

**If starts are offset by one bar, stop and fix the parser before anything else.**

#### P3.10 Report

Write `research/notes/p3_data_report.md` covering:
- coverage;
- gaps;
- the cross-source results;
- events coverage by month;
- the universe caveats (survivorship, market-cap band, surveillance history, sector-map bias).

**Acceptance:** at least 95% valid sessions for at least 90% of the universe; the cross-source check passes; the report is written.

**STOP.**

### P4: Features and point-in-time calibration

#### P4.1 `timeprofile.py`

- **`slot_variance`:** the winsorised mean of squared 15-minute log returns per slot.
  - Lookback 60 sessions, `min_valid` 40, slots 1..23 only.
- **`diurnal_shape`:** the universe shape, normalised to mean 1 over slots 1..23.
- **`cum_rvol`:** cumulative volume through bar t divided by the median of the same quantity over the prior 20 sessions (at least 15 valid).
- **Failure:** each returns `None` plus a reason when data is missing or zero, or the history is too short.

#### P4.2 `calibration.py`: `CalibrationProvider`

Computes, per (symbol, session), from prior sessions only:
- β (A.7), with n0 = 300 (ASSUMPTION);
- s², s²px and the shape;
- cumulative-volume medians;
- NIFTY per-slot variance, and the 80th percentile of |Z_M| by slot;
- VIX_ref;
- the cluster id (the weekly `as_of` must precede the week).

Every calibration object carries `n_sessions` and `valid`.

#### P4.3 `events.py`

- `news_since(symbol, from_ts, to_ts)` returns `bool | None`.
- `scheduled_event(symbol, session, as_of_ts)` returns `bool | None`.
- `None` means unknown, and every strategy treats unknown as blocked.

#### P4.4 `session_cache.py`

- Per session, compute causal numpy arrays for all symbols: r, f, e, E, V, Z, RVOL_cum, Z_M.
- Keep an optional parquet cache keyed by (session, config hash), stored under `research/outputs/`.

#### Tests

**Known-answer:**
- β recovered from synthetic data;
- shape normalisation;
- RVOL computation.

**Look-ahead:**
1. Changing data from session d onward leaves session d's calibration unchanged.
2. Changing bars after t leaves Z[t] unchanged.
3. The adapter-level check from D17.

**Fail-closed:** NaN, inf, None, bool, zero volume, a missing slot, too few sessions.

**Development data:** use synthetic generators. The 32-session file must produce `DATA_INVALID` for RESID_REV, and that is expected.

### P5: Strategies

#### P5.1 `research/strategies/orb_prod.py`

Imports the production code **read-only**:
- the logic of `track2_orb_signal_adapter`;
- `LiquidMomentumEngine.evaluate_15m_orb_breakout`;
- `MarketRegimeFilter.evaluate_regime`;
- `calculate_position_size`.

**Entry gates:**
- eligible bars 09:30–14:30;
- the first qualifying bar per symbol-day;
- volume ratio against the slot median ≥ the regime multiple. Production passes no breadth data, so the multiple is 3.5× in practice; replicate that.
- extension ≤ 0.5 × ATR14;
- regime from the NIFTY opening range.

**Stop:** max(OR low, entry − 1.5 × ATR14), with a 0.5% SL-limit offset.

**Exits:** two-tranche 1.5R/3R, breakeven after T1, policy exit at 15:05.

**Manifest:** record the git blob hash of every imported file.

#### P5.2 `research/strategies/resid_rev.py`: RESID_REV v1

- Implement Appendix B exactly. Every parameter comes from the YAML; there are no hidden defaults.
- **One signal per symbol-day.** Only an **emitted** signal counts toward the limit. A bar that crosses |Z| ≥ z* but fails a filter does not use up the day. Every evaluation is logged with its reason.
- **Output:** a `SignalIntent` with:
  - side and stop trigger;
  - targets `[(T1, 0.5), (T2, 0.5)]`;
  - `max_bars` = h_eff (A.9);
  - `is_shadow` = True;
  - a `priority_score` of |Z|;
  - `diagnostics`: Z, E, σ_E, β, RVOL, Z_M, factor used, factor weight, φ (logged only), expiry-day flag, VIX.

#### P5.3 `ORB_SIMPLE`

Rename the existing simplified `OrbAdapter` to `ORB_SIMPLE`. Keep it for comparison only.

#### Tests (synthetic sessions, hand-computed prices)

**Signals:**
- An idiosyncratic −3σ dislocation on normal volume, no news and a turn bar gives BUY with the exact T1, T2, stop and h_eff.
- The mirror case gives SELL.

**Rejections:**
- RVOL 2.0 → `VOLUME_ABNORMAL`.
- A filing since the previous close → `NEWS_BLOCKED`.
- An events provider returning `None` → `NEWS_BLOCKED`.
- Calibration with n < 40 → `DATA_INVALID`.
- A decision at 13:45 → `WINDOW_CLOSED`.
- |Z| < z* → `NO_SETUP`.
- A bar that still extends the move → `NO_TURN`, and a later turn bar the same day may still signal.
- A required stop above 2% → `STOP_TOO_WIDE_SKIP`.
- T1 closer than 0.40% → `COST_HURDLE`.
- A NIFTY shock → `MARKET_FILTER`.
- A second emitted signal the same day → blocked.

**ORB_PROD:** matches the production adapter's decisions on 10 sampled symbol-days.

### P6: Decision engine (`research/decision/`)

#### P6.1 `ledger.py`

- **Storage:** SQLite (WAL mode, append-only), kept under `TRACK2_HISTORY_DIR` or `research/outputs/`.
- **Unique key:** (run_id, mode, strategy_version, symbol, decision_ts). `signal_id` = sha1 of those fields.
- **Scope:** every intent is written, whatever its disposition.

| Group | Fields |
|---|---|
| Identity | `signal_id`, `run_id`, `mode` (BACKTEST, SHADOW or PAPER), `strategy_id`, `strategy_version`, `prereg_sha` |
| Decision | `session`, `decision_ts`, `symbol`, `side`, `decision_price`, `entry_ref`, `stop_trigger`, `stop_limit`, `t1`, `t2`, `h_eff`, `time_exit_ts` |
| Sizing | `qty_planned`, `notional_planned`, `risk_rs_planned`, `r_basis`, `vix_multiplier` |
| Allocation | `disposition`, `allocated` |
| Fill | `entry_fill_price`, `entry_fill_ts`, `evidence_class` |
| Exit | `exit_reason` (includes `GAP_THROUGH_LIMIT`), `exit_fills` (JSON) |
| Rupee P&L | `gross_pnl_rs`, `fees_rs`, `slippage_rs`, `rms_fee_rs`, `net_pnl_rs` |
| R outcomes | `gross_r`, `fee_r`, `slip_r`, `net_r`, `mfe_r`, `mae_r` |
| Provenance | `features` (JSON), `data_hash`, `config_hash`, `code_commit` |

#### P6.2 `estimator.py`

- **`StrategyEstimate`** holds:
  - n and n_sessions;
  - `mean_net_r` and `se_cluster`. Use a new `clustered_se` helper in `research/decision/stats.py`, because `metrics.clustered_t` returns only t. Do not modify the locked gate.
  - `lb95 = mean − 1.645·se`;
  - `mean_gross_r`;
  - `shrunk_gross_r = n/(n+85) · mean_gross_r`, where n0 = 85 is an ASSUMPTION: a prior worth 85 trades centred on zero edge;
  - `r_basis` and `as_of`.
- **`expected_net_r(signal)`** = shrunk_gross_r − fee_r(signal) − slip_r(signal), with each term computed from that signal's own stop distance.

#### P6.3 `sizing.py`

**`vol_target_multiplier(vix_t, vix_ts, vix_daily_history, now)`** (A.10):

- **Normal case:** m = clip(VIX_ref/VIX_t, 0.5, 1.0), where VIX_ref is the median of the prior 250 daily closes (at least 120 required).
- **Overnight jump:** if VIX rose 15% or more overnight, m = 0.5.
- **Missing data:** if VIX is stale (more than 5 minutes old), missing or invalid, m = 0 and no new entries are allowed.
- The +15% threshold, the 5-minute limit and the 0.5 floor are ASSUMPTIONs; register them.
- Final quantity = floor(m × qty). **m is never above 1.**
- Do not use `vix_regime.py` multipliers.

#### P6.4 `clusters.py`

- **Frequency:** weekly, with `as_of` before the week starts.
- **Input:** correlations of **market-residual** 15-minute returns (bars 1..23) over the prior 60 sessions.
- **Method:** **average linkage** at ρ > 0.4, with a maximum cluster size of 10% of the eligible universe; split any larger cluster by complete linkage.
- **Why not single linkage:** it chains. A factor simulation with 180 names and mean ρ = 0.29 produced one cluster of 139 names, which would collapse a 3-slot book under the cap.
- **Tests:**
  - synthetic correlated blocks are recovered;
  - no cluster holds more than 10% of names on data with a factor structure.

#### P6.5 `allocator.py`

Runs at every bar close. Rules:

1. **Same symbol, opposite sides:** drop both, unless |ΔE[R]| ≥ 2 × SE_diff.
2. **Same symbol, same side:** keep the one with the higher `expected_net_r`.
3. **Ranking:**
   - **SHADOW mode:** by the pre-registered priority. Estimates start at 0, and every signal is logged regardless.
   - **EXPLOIT mode** (promoted strategies only): drop any candidate with `expected_net_r` ≤ 0, then rank by `expected_net_r` × risk_rs × m.
4. **Greedy fill**, subject to:
   - at most 3 slots;
   - at most ₹58,333 notional per position;
   - total notional no more than free cash;
   - at most 1 position per cluster and at most 2 per sector.
5. **Log every drop reason.**

**Tests:**
- renaming symbols does not change the result;
- every constraint binds when it should;
- the conflict rules;
- EXPLOIT mode never allocates a candidate with `expected_net_r` ≤ 0.

#### P6.6 `promotion.py`: the only writer of `register.json`

**State machine:**
```
UNVERIFIED --(lock-verified holdout record: pass)--> SHADOW
UNVERIFIED --(lock-verified holdout record: fail)--> REJECTED        (terminal for this version)
SHADOW     --(n_admissible >= n_pre/2 and mean + 1.2816*SE < 0)--> KILLED   (futility; terminal)
SHADOW     --(n_admissible reaches n_pre: ONE terminal test)--> PROMOTE_TO_PAPER if evaluate_gate passes and LB95 > 0,
                                                                  else KILLED
```

**Evidence rules:**
- The holdout transitions use the E1 study record from `run_holdout.py`, and only when its lock verifies. This is the only place E1 evidence can change a status, and it can never promote.
- `evaluate_gate`, LB95 and the futility check use **E2/E3 rows only**.
- There are no repeated looks after n_pre. A strategy that fails is KILLED.
- A new idea needs a new version, a new pre-registration and new data.
- Why: repeated weekly re-tests at n = 111, 121, …, 311 would inflate false promotions from 2.3% to about 7.6%.

**DecisionRecord** (JSON in `records/`) contains:
- inputs;
- the ledger hash;
- the gate output;
- LB95, PSR and DSR (definitions in A.14);
- the status, with reasons.

**Tests** use synthetic ledgers at σ = 0.74, with the session structure included:
- **False promotions:** at a true mean of 0 and n = 85, the promotion rate must be ≤ 3.5%, over **10,000** simulations. The true rate is about 2.4–2.6%.
- **Power:** at a true mean of +0.20R and n = 111 with independent trades, the promotion rate must be 0.80 ± 0.03.
- **Futility:** at a true mean of −0.20R, KILLED must fire by n_pre/2 in at least 60% of runs. It measured 77% on 25 Sep.

#### P6.7 `stress.py`

**Model:** a t-copula Monte Carlo (A.17), with at least 10⁶ draws and a fixed seed.

**Window:** one 15-minute bar.

**Inputs:**
- **p0:** the per-bar stop hazard, taken from the ledger (stops ÷ position-bars). Until data exists, use 1.25% per bar (DERIVED from A.4: 23.2% of trades stop within about 21 bars).
- **Parameters:** ν ∈ {3, 5, 8}, ρ ∈ {0.30, 0.45, 0.60}, 3 positions.
- **Loss severities:** the empirical gap-through and `GAP_THROUGH_LIMIT` loss distributions, plus a band-hit scenario (a −10% band, SL-limit unfilled, exit at the band).

**Outputs:**
- P(k of 3 stopped);
- tail-loss quantiles at 95%, 99% and 99.9%, in ₹ and as a share of the corpus;
- a comparison with the ₹4,500 risk-to-stop budget.

**Never claim a guaranteed maximum loss.**

**Tests:**
- The **Gaussian** copula (ν → ∞) matches the binomial at ρ = 0.
- For finite ν, the result matches the mixture formula C(3,k) · E[F^k (1−F)^(3−k)], with F = Φ(q·√(W/ν)). A t-copula with ρ = 0 is **not** independent: the shared W makes it 49–249 times the binomial for 3 of 3 at p0 = 1%.

### P7: Studies (design set → frozen pre-registration → one holdout run)

#### P7.1 Split (MEASURED facts drove this choice)

| Set | Sessions | Notes |
|---|---|---|
| Design | first available (about Oct 2021) → 2024-09-30 | |
| Holdout | 2024-10-01 → **2026-07-31** | Pre-CAS. It ends before the audit window. |
| Post-CAS | from 2026-08-03 | Excluded from both. This covers the 32 audited sessions and the closing-auction regime change. Report these sessions separately as descriptive data only; prospective shadow data takes over from there. |

The HoldoutGuard (P3.7) enforces the split. Yashu may change the dates **before** P7.2 (decision 2).

#### P7.2 Design-set tasks

These are the only tasks allowed on the design set. Register every variant.

1. **ORB_PROD:** R statistics and the daily P&L series. It has no parameters.
2. **RESID_REV z\*:**
   - Take the 95th percentile of the per-stock-day maximum |Z_t| over t ∈ [2, 16], across all eligible design stock-days, **before** the volume, news and market filters.
   - Freeze it to 2 decimal places.
   - The Gaussian iid reference value is 2.59.
   - No returns are used, so z* is not fitted to outcomes.
3. **Holding period h:**
   - Event study of the residual reversion curve at h ∈ {1, 2, 4, 8, EOD} bars.
   - Compute full-rule mean net R for h ∈ {4, 8, EOD}.
   - Choose the h with the highest mean net R and freeze it. This counts as 3 trials, and all three are registered with the same `sample_id`.
4. **Data-quality exclusions:** decided on design data only.

#### P7.3 Freeze (STOP)

1. Write and commit `research/studies/prereg/resid_rev_v1.yaml` (Appendix B, with z\* and h filled in).
2. Write and commit `resid_rev_v1.lock`, containing the commit hash and the YAML's SHA-256.
3. Send Yashu `research/notes/p7_design_report.md` and the YAML. Wait for "continue".

#### P7.4 Holdout run (exactly once)

`run_holdout.py`:
- refuses to run if the lock is missing or doesn't match;
- refuses to run if the run marker `research/studies/prereg/resid_rev_v1.holdout_done` exists;
- writes and commits that marker after the run.

It runs ORB_PROD_v1 and RESID_REV_v1:
- **Primary:** long/short, 1 slippage tick per side, `r_basis="stop_limit"`.
- **Registered secondaries:**
  - 2 ticks of slippage;
  - long-only;
  - trigger basis.

**Outputs:**

1. **Primary statistic:** mean net R per **emitted signal**, from the unconstrained per-signal simulation (`signal_sim.py`, D15). All signals are simulated with identical fill rules.
   - A clamp miss counts as 0R: no trade, no cost.
   - Report alongside it: the mean per filled trade, the miss rate, the day-clustered t and CI, and the median.
   - Also report:
     - exit-type shares, including `GAP_THROUGH_LIMIT`;
     - fees and slippage as a share of gross;
     - by year;
     - by VIX tercile (descriptive only);
     - long vs short;
     - capacity: qty ÷ bar volume at p50 and p95.
2. **Portfolio simulation:**
   - Settings: `allow_shadow=True`, 3 slots, SHADOW allocator priority, VIX multiplier, cluster cap.
   - Report: Sharpe, max drawdown, Calmar.
3. **Correlation between RESID_REV and ORB_PROD daily P&L:**
   - Compute it on the slot-limited series and on the unconstrained per-signal series.
   - Give both a Fisher CI and a day-block bootstrap CI.
4. **DSR and PBO:**
   - **Family DSR:** N = 3 (the h variants), with V[SR] from those same-sample variants.
   - **Desk DSR:** N = the registry count, with V[SR] from same-`sample_id` trials only.
   - **PBO:** via CSCV on the design variants.

#### P7.5 Decision rule (pre-registered)

RESID_REV_v1 moves UNVERIFIED → SHADOW, through `promotion.py` with the holdout record, if **both** hold:
- the primary day-clustered t is ≥ 2.0 and the mean is > 0;
- the mean stays > 0 with 2 ticks of slippage.

Otherwise it moves to REJECTED. Write the report and stop. Do not re-tune, and do not run a second holdout.

**STOP.** Send Yashu `research/notes/p7_holdout_report.md`.

### P8: Shadow integration (Rule 8)

#### P8.1 Production patches

- Write them on the worktree branch, with tests, as review requests. **Do not merge.**
- **Fixes to include:**
  - a side field end to end;
  - short support for F&O stocks in the governor (SEBI short-selling framework, 5 Jan 2024);
  - a ₹58,333 cap per slot, with total notional no more than cash;
  - MIS as the default product;
  - one fee function (settle the 0.0030699% vs 0.00297% question first; see C.1);
  - a flat enforced at 15:05/15:08;
  - removal of the 1,000-share RVOL floor;
  - correct `MultiStrategyEngine` method names.
- **Review request:** `shared/reviews/track2_decision_engine_review_request_<date>.md`, for Codex. Antigravity integrates.

#### P8.2 `research/shadow/run_day.py`

**Inputs:**
- **Live bars:** `shared/track2_liquid/live_candles_track2.json`, from the Dhan feed bridge. Verify its schema first.
- **VIX and sector indices:** if they are not subscribed yet, adding them is a production change and needs a review request.
- **Announcements poller:** checks at least every 60 seconds during market hours, and only if decision 7 approves the source. A stale feed blocks trading.

**Each bar:** evaluate ORB_PROD and RESID_REV at the bar close + 60 s, and write ledger rows.

**End of day:**
- record E1 outcomes;
- produce E2/E3 evidence through `research/execution_realism` wherever depth or fills exist;
- write `shared/track2_liquid/shadow_reports/YYYY-MM-DD.md`.

#### P8.3 Data recording

Record from now on:
- depth snapshots;
- pre-open data from 09:00 to 09:15, tagged with the auction rules in force since 7 Sep 2026;
- CAS indicative data from 15:15 to 15:35;
- the announcements feed.

This touches production daemons, so **file its review request now.** It waits for Rule 8, not for P7.

#### P8.4 Weekly reviews

Run `promotion.py` weekly. `register.json` is the only source of truth for strategy status.

**STOP.**

### P9: Later strategies

Only after the P7 result, and each with its own pre-registration and new data.

**Candidates:**
- **ORB_IN_PLAY:** catalyst plus top-5 RVOL.
- **PRECAS_MOM:** momentum from 14:45 to 15:05.
- **PEAD_D0:** day-0 earnings drift, with an implied-move SUE.
- **Gap fade:** pre-open data from 7 Sep 2026 onward only.
- **Pairs:** a hedged variant of RESID_REV. Build it only if RESID_REV's measured μ̂ and the pairs' measured ρ put it above the A.12 threshold ρ\*.

**Not alphas; do not build them as such:**
- **GEX flip and max pain:** the dealer sign can't be identified; stock options expire monthly and settle physically; the Jane Street precedent.
- **MLOFI:** usable only as an execution filter.
- **Basis/CVD.**
- **"European open":** at most, a DST natural-experiment study.

---

## 6. Test plan: known-answer values

| Test | Expected | Label |
|---|---|---|
| `DhanFeeEngine` round trip, ₹583.33 × 100 | ₹61.99 (0.1063%); ₹61.85 at 0.00297% | MEASURED |
| p\* at c = 0.074 | q = 0: 0.6137; q = 0.1: 0.5653; q = 0.5: 0.4296 | DERIVED A.3 |
| Barrier model: a = 1, b = 1.5, σ = 0.3655 R/√h, T = 5.25 h, μ = 0 | survival 0.694; P(stop by T) = 0.232; P(T1 by T) = 0.073 (Monte Carlo at 1-minute steps: 0.702 / 0.227 / 0.070) | DERIVED A.4 |
| Null scan, Gaussian iid | P(max\|Z\| ≥ 2.5, k ≤ 23) = 0.081 ± 0.003; over 2 ≤ k ≤ 16: 0.062 ± 0.003; window 95th percentile: 2.59 ± 0.01 | DERIVED A.8 |
| Power (independent trades, one-sided 5%, 80%) | δ = 0.05: 1,355; 0.10: 339; 0.20: 85; 0.30: 38 | DERIVED A.13 |
| Power under t ≥ 2.0 (independent trades) | δ = 0.20: 111; δ = 0.10: 443; power at n = 85, δ = 0.20: 0.69 | DERIVED A.13 |
| E[MDD] at zero edge, σ = 0.74, N = 250 | 14.66R continuous; 13.7R ± 0.2 discrete | DERIVED A.16 |
| Pair ρ\*, k = 1, c = 0.156% | μ = 0.4%: 0.908; 0.6%: 0.625; 0.8%: 0.458; 1.2%: 0.292 | DERIVED A.12 |
| Pair ρ\*, c = 0.106% | 0.638; 0.410; 0.299; 0.192 | DERIVED A.12 |
| Fisher 95% half-width at ρ ≈ 0 (after tanh) | n = 32: 0.349; n = 250: 0.124 | DERIVED A.18 |
| DSR worked example (A.14 conventions: pooled σ = 0.74, normal moments) | SRs −0.004, −0.349, −0.716, −0.104, +0.305, −0.468, −0.128 give SD 0.3336; SR0 = 0.700 at N = 32 (0.64 at N = 21); DSR(LAST_LIGHT, n = 11) = 0.111 (0.15 at N = 21). Reproduced by `probabilistic_sharpe(0.305, 11, 0, 3, expected_max_sharpe(32, 0.3336**2))` | DERIVED A.14 |
| Audit regression (P2) | 103 events; 18 / 10 / 75; −0.084R net, −0.003R gross | MEASURED |
| t-copula at ρ = 0, p0 = 1%, 3 of 3 (2M draws) | ν = 3: 2.5e-4; ν = 5: 9.5e-5; ν = 8: 4.9e-5 (binomial: 1.0e-6) | DERIVED A.17 |

---

## 7. Acceptance checklist

- [ ] P0: worktree created; ignore rules committed first; baseline and provenance recorded.
- [ ] P1: facts file; registry seeded with 21 rows plus `sample_id`; erratum; register with single-writer test.
- [ ] P2: D1–D19 fixed, each with a test that failed first; both regressions pass or have documented deviations; runtime reported.
- [ ] P3: decision 7 answered; ≥ 95% valid sessions for ≥ 90% of the universe; Kite cross-check passes; events are point-in-time (intimation keys); universe caveats stated.
- [ ] P4: all three kinds of look-ahead test pass; fail-closed tests pass.
- [ ] P5: ORB_PROD matches production on sampled days; RESID_REV hand-computed tests pass; every parameter comes from the YAML.
- [ ] P6: decision tests pass (10,000-simulation false-positive test, power, futility, copula mixture); single writer confirmed.
- [ ] P7: z\* and h frozen on the design set; lock committed; holdout run exactly once, with the marker committed; report labelled.
- [ ] P8: review requests filed; nothing merged into production without Rule 8 sign-off.

---

## 8. Open decisions for Yashu

1. **Dhan Data API subscription** (₹499 + taxes a month). Needed for P3.
2. **Holdout window.** Default is 2024-10-01 → 2026-07-31, with post-CAS sessions excluded. Decide before P7.2.
3. **Shadow sample size n_pre under the locked gate.**
   - 85 executions give 69% power for +0.20R; 111 give 80%, assuming independent trades.
   - Day clustering needs more (about 144 at 2 trades per session and ρ = 0.3).
   - Recommended: set n_pre from a clustered simulation on the holdout ledger's design effect, with 111 as the floor.
4. **Shorts.** Allowed in research. Allowing them in paper trading needs the P8 governor patch and Codex review.
5. **Tail-loss limit X.** The control would read: "risk-to-stop ≤ ₹4,500 **and** modelled 99.9% intraday tail ≤ X". Set X after the P6.7 table.
6. **Market-cap band.** Default: not applied historically unless a shares-outstanding history is supplied.
7. **NSE website JSON APIs** (announcements, board meetings, corporate actions).
   - These are the site's own endpoints, not a licensed feed. Approve their use at 1 request per 2 s, or name another source: a data vendor, or NSE archive files only.
   - Without them, RESID_REV can only be tested as `RESID_REV_NF` (no news filter).
8. **CNC (overnight) strategies**: `PEAD_DRIFT` and `CAS_REVERSAL` from 3bc73a4.
   - Track 2 is MIS and flat by 15:08.
   - Default: out of scope.
   - Allowing them would mean a separate sleeve with its own cost model (₹144–159 per round trip), gap risk and a separate gate.

---

## Appendix A: Mathematical reference

**Notation.**
- Bars b = 0 … B−1 by start time; bar 0 is 09:15–09:30. C[b] is the close of bar b.
- The decision is at the close of bar t (t = 2 is 10:00; t = 16 is 13:30).
- sg = +1 for BUY, −1 for SELL.
- Every R value states its basis.

**A.1 Fees per order (`DhanFeeEngine`).**
```
brokerage = min(20, 0.0003 * turnover)          (MIS)
STT       = 0.00025 * turnover                  (SELL leg only)
exchange  = 0.000030699 * turnover              (verify the current NSE rate; configurable)
SEBI      = 0.000001 * turnover
stamp     = 0.00003 * turnover                  (BUY leg only)
GST       = 0.18 * (brokerage + exchange + SEBI)
RMS fee   = 20 * 1.18 = 23.60 per auto-squared order (should never occur under the 15:05 policy exit)
```
Brokerage hits its ₹20 cap at ₹66,667 per leg, which is above the ₹58,333 slot. Inside the slot, friction is a constant 0.1063%.

**A.2 Friction in R.**
- Trigger basis: c = fee_fraction / s, where s is the stop distance. s = 1.45% gives 0.073R; 1.00% gives 0.106R; 0.80% gives 0.133R.
- Stop-limit basis: the distance is roughly s + 0.5%, so at s = 0.8%, c ≈ 0.082R, and a normal stop-out costs about −0.62R.
- State the basis every time.

**A.3 Two tranches with no time exit.** Let p = P(T1) and q = P(T2 | T1). The three payoffs are −1 − c, 0.75 − c and 2.25 − c.
```
E[R] = p*(1.75 + 1.5q) - (1 + c)      =>      p* = (1 + c) / (1.75 + 1.5q)
```

**A.4 Doubly absorbed Brownian motion.** X_t = μt + σW_t in R units, with the stop at −a, T1 at +b and L = a + b. With μ = 0:
```
survival(T) = sum over odd n of (4/(n*pi)) * sin(n*pi*a/L) * exp(-(n*pi*sigma)^2 * T / (2*L^2))
```
- The absorption split into stop and T1 at finite T needs the first-passage series or Monte Carlo. The T → ∞ limit is b/L to a/L = 0.6 : 0.4.
- Calibration (MEASURED inputs): σ = 0.53%/1.45% = 0.3655 R/√h, T = 5.25 h (09:45 to 15:00), a = 1, b = 1.5.
- Result: survival 0.694; P(stop) 0.232; P(T1) 0.073. Measured: 0.728 / 0.175 / 0.097, with χ² p ≈ 0.23.

**A.5 Optional stopping.**
- With constant drift μ and any bounded exit rule holding a position fraction w_t, E[gross R] = μ · E[∫₀^τ w_t dt]. At μ = 0, every exit architecture returns −c − slippage.
- With a drift that depends on state, E[∫ μ_t w_t dt] contains a covariance term, so exits can add value only by timing that drift. The MC audit makes the same point ("zero drift is not zero edge").
- RESID_REV's premise is exactly such a drift: a reversion that follows a dislocation.

**A.6 Time-of-day normalisation.** Over slots 1..23:
```
s2px[b]  = winsorised mean over prior 60 sessions of r[b]^2                 (per symbol, per slot)
shape[b] = mean over symbols of ( mean_d e[i,d,b]^2 / s2_i ), normalised to mean 1 over slots 1..23
RVOL[t]  = sum_{b=0..t} vol[b] / median over prior 20 sessions of the same cumulative sum
```
- Minimum valid history: 40 sessions for the variances, 15 for RVOL.
- These replace every "× session ATR" gate.

**A.7 RESID_REV residual path.**
```
r[b]  = ln(C[b]/C[b-1]),  f[b] = ln(I[b]/I[b-1])            b = 1..t   (I = factor index; excludes the overnight gap)
beta  = clip( w*beta_ols + (1-w)*1.0 , 0.3, 2.5 ),  w = n/(n+300)     (OLS on bars 1..23 of the prior 60 sessions)
e[b]  = r[b] - beta*f[b]
v[b]  = s2_i * shape[b]                                     (s2_i: winsorised mean e^2, prior 60 sessions)
E[t]  = sum_{b=1..t} e[b],   V[t] = sum_{b=1..t} v[b],   Z[t] = E[t]/sqrt(V[t])
ZM[t] = ln(N[t]/N[0]) / sqrt(sum_{b=1..t} s2N[b])            (NIFTY 50 with its own slot variances)
```

**A.8 Scanning bias and z\*.**
- For iid Gaussian increments, P(any |Z| ≥ 2.5) is 0.081 over 23 returns and 0.062 inside the window. A single look gives 0.0124.
- z\* = the design-set 95th percentile of the per-stock-day window maximum. The Gaussian reference is 2.59.

**A.9 RESID_REV prices and holding window.** P0 = C[t].
```
T1 = P0 * exp( sg * 0.5 * |E[t]| )        rounded toward P0 (never overstate the gain)
T2 = P0 * exp( sg * 1.0 * |E[t]| )        rounded toward P0
h_eff = min(h, 22 - t)  for h in {4, 8};   h = EOD -> h_eff = 22 - t
        (bar 22 = 14:30-14:45 is the last bar fully held; the 15:05 policy exit falls in bar 23)
sigma_hold = sqrt( sum_{b=t+1}^{t+h_eff} s2px[b] )
s = max(0.008, 1.0 * sigma_hold);   if 1.0 * sigma_hold > 0.020 -> STOP_TOO_WIDE_SKIP
stop = P0 * (1 - sg * s)                  rounded away from P0 (never understate the loss)
breakeven after T1 = actual entry fill price
gate: |T1 - P0| / P0 >= 0.40%
slippage: entry at next open + sg*k ticks; stop fills at trigger - sg*k ticks; time/policy exits at open - sg*k ticks;
          targets fill at the limit price (they are resting limits filled by trade-through)
```

**A.10 Sizing and the volatility-targeting multiplier.**
```
qty = floor( min( 1500 / |entry_ref - stop_limit| , 58333.33 / entry_ref , free_cash / entry_ref ) )
m   = clip( VIX_ref / VIX_t , 0.5 , 1.0 );   m = 0.5 if overnight dVIX >= +15%;   m = 0 if VIX missing/stale/invalid
qty_final = floor(m * qty)
```
- The notional cap binds on 96% of trades, so rupee risk ≈ notional × stop%. Stops scale with volatility, so rupee risk rises with VIX unless m offsets it.
- Moreira & Muir (2017) support volatility management.
- Nagel (2012) finds that reversal premia rise with VIX. That contradicts a "low VIX → mean reversion" switch.

**A.11 Estimator.**
```
mean_net_r;  se_cluster (CR1 by session, research/decision/stats.py);  LB95 = mean - 1.645*se
shrunk_gross_r = n/(n+85) * mean_gross_r;   expected_net_r(signal) = shrunk_gross_r - fee_r(signal) - slip_r(signal)
```

**A.12 Pair vs single leg.** Use a minimum-variance hedge h = ρσ_A/σ_B, with k = σ_A/σ_B and c = all-in cost of one leg.
```
naked: SR1 = (mu - c)/sigma_A
pair : SR2 = (mu - c(1 + rho*k)) / (sigma_A * sqrt(1 - rho^2))
pair wins iff f(rho) = mu - c(1 + rho*k) - (mu - c)*sqrt(1 - rho^2) > 0
```
- f(0) = 0, f'(0) = −ck, and f is convex. So there is a single threshold ρ\*, and it exists only when μ > c(1 + k).
- **The conclusion depends on μ.** At c = 0.106%, a pair beats the naked leg at ρ ≈ 0.3–0.5 only if the expected gross reversion μ ≥ 0.6–0.8% per trade. μ is the realised mean reversion, not the target distance |E|. Decide from measured μ̂ (P9).

**A.13 Power.**
```
n = ceil( ((z_alpha + z_beta) * sigma_R / delta)^2 )          (independent trades)
one-sided 5%, 80%: z = 1.645 + 0.8416;     t >= 2.0 gate: z = 2.0 + 0.8416
power(n, delta) = Phi( delta*sqrt(n)/sigma_R - z_alpha )
```
- With clustering, multiply n by the design effect 1 + (m − 1)·ρ_w, where m is trades per session and ρ_w is the within-session outcome correlation.
- Or simulate the power through `evaluate_gate`.

**A.14 Deflated Sharpe (Bailey & López de Prado 2014).**
```
DSR = PSR(SR, T, skew, kurt; SR0),    SR0 = sqrt(V) * ( (1-gamma)*Phi^-1(1-1/N) + gamma*Phi^-1(1-1/(N*e)) ),  gamma = 0.5772
```
- **N:** the number of trials in the relevant registry scope.
- **V:** the variance of per-trade SR across trials **on the same sample** (same `sample_id`). Pooling trials from different samples mostly measures sampling noise.
- **SR:** per trade, as mean ÷ sd. The worked example in section 6 uses a pooled σ of 0.74 and normal moments; state which convention each report uses.

**A.15 Combining two strategies.**
```
equal risk: SR = (S1 + S2) / sqrt(2(1 + rho));   optimal: SR = sqrt((S1^2 + S2^2 - 2*rho*S1*S2)/(1 - rho^2));   add 2 iff S2 > rho*S1
```
With S1 = 0 (the measured ORB), an equal-risk blend beats strategy 2 alone only if ρ < −0.5.

**A.16 Drawdown (Magdon-Ismail et al. 2004).**
- At μ = 0, continuous time: E[MDD] = √(π/2)·σ·√N, which is 14.66R for σ = 0.74 and N = 250. A discrete walk gives 13.7R.
- For large N with μ > 0: E[MDD] ≈ (σ²/μ)·(0.5·ln(μ²N/(2σ²)) + 0.9818).
- With a negative net drift, drawdown grows roughly linearly: about 25R at −0.08R per trade over 250 trades.

**A.17 Equicorrelated t-copula.**
```
X_i = sqrt(nu/W) * ( sqrt(rho)*Z + sqrt(1-rho)*eps_i ),   W ~ chi2(nu);   stop_i <=> X_i < t_nu^{-1}(p0)
```
The shared W creates tail dependence even at ρ = 0. Check against the mixture formula (P6.7), not against the binomial.

**A.18 Correlation uncertainty.**
```
Fisher: z = atanh(r), se = 1/sqrt(n-3), CI = tanh(z +/- 1.96*se)   (half-width at r=0: n=32 -> 0.349; n=250 -> 0.124)
day-block bootstrap: resample sessions with replacement, B = 10,000, fixed seed
```

---

## Appendix B: Pre-registration templates

**`research/studies/prereg/resid_rev_v1.yaml`.** It stays in DRAFT until P7.3. P7.2 fills in `z_star` and `hold_bars`.

```yaml
id: RESID_REV_v1
status: DRAFT
hypothesis: >
  Mean net R per emitted signal (unconstrained per-signal simulation, identical fill rules for all
  signals, clamp misses counted as 0R, 1-tick slippage per side, DhanFeeEngine MIS fees,
  r_basis stop_limit) is greater than 0 on the holdout.
primary_test: {statistic: day_clustered_t, threshold: 2.0, also_require: mean_net_r > 0}
secondary_tests:
  - {name: slippage_2_ticks, require: mean_net_r > 0}
  - {name: long_only, require: report_only}
  - {name: r_basis_trigger, require: report_only}
variants_registered: [RESID_REV_NF]      # run only if the announcements history is unavailable (P3.4)
data:
  bars: {interval_min: 15, source: dhan_v2_charts_intraday, timestamps: bar_start_IST, slots_used: "returns 1..23"}
  design: [first_available, "2024-09-30"]
  holdout: ["2024-10-01", "2026-07-31"]
  excluded: {from: "2026-08-03", reason: "post-CAS regime and audited sessions; descriptive only"}
universe:
  track2_point_in_time: true
  min_price: 10
  dtv20_cr: 30
  exclude: [fo_ban]
  exclude_if_history_available: [asm, gsm]
  mcap_band: {range_cr: [4000, 75000], apply_only_if: lagged_shares_history_available}
factor: {primary: mapped_sectoral_index, fallback: NIFTY50, use_nifty_if_stock_weight_above: 0.10}
calibration:
  beta: {lookback_sessions: 60, bars: "1..23", shrink_n0_pairs: 300, prior: 1.0, clip: [0.3, 2.5]}
  resid_var: {lookback_sessions: 60, winsor_pct: 99, min_valid: 40}
  shape: {universe: true, lookback_sessions: 60, normalise_mean: 1, bars: "1..23"}
  price_var_by_slot: {lookback_sessions: 60, min_valid: 40, winsor_pct: 99}
  rvol: {lookback_sessions: 20, min_valid: 15}
  market_filter: {index: NIFTY50, percentile: 80, lookback_sessions: 60}
signal:
  decision_window_bar_close: ["10:00", "13:30"]   # t = 2..16
  z_star: null                     # P7.2b: 95th pct of per-stock-day max|Z| on design set (Gaussian ref 2.59)
  turn_confirmation: true          # e[t] * E[t] < 0
  rvol_band: [0.7, 1.5]
  news:
    any_filing_since: prev_trading_day_15_30   # dissemination timestamp <= decision time
    scheduled: [results, board_meeting]        # today or next trading day, keyed by intimation timestamp
    ex_date_today: block
    unknown: block
  market_filter: "|ZM[t]| <= 80th percentile of |ZM| at the same bar, prior 60 sessions"
  one_emitted_signal_per_symbol_day: true      # failed evaluations do not consume the day
  side: "SELL if E[t] > 0 else BUY"
  shorts: allowed
trade:
  entry: {rule: next_bar_open, slippage_ticks: 1, clamp_bps_adverse: 10}
  stop: {sigma_hold_multiple: 1.0, floor_pct: 0.8, skip_if_above_pct: 2.0, sl_limit_offset_pct: 0.5}
  targets: [{retrace_of_E: 0.5, fraction: 0.5}, {retrace_of_E: 1.0, fraction: 0.5}]
  breakeven_after_t1: {enabled: true, reference: entry_fill_price}
  min_t1_distance_pct: 0.40
  hold_bars: null                  # P7.2c: chosen from {4, 8, EOD}; h_eff = min(h, 22 - t) (Appendix A.9)
  policy_exit: "15:05 (SessionPolicy)"
  gap_through_limit: {model: escalate_at_open, slippage_ticks: 1, count_separately: true}
engine: {var_elm_rate: 0.20, allow_shorts: true, r_basis: stop_limit}
costs: {fees: DhanFeeEngine_MIS, slippage_ticks_per_side: 1, sensitivity_ticks: [0, 2, 3]}
sizing: {risk_budget_rs: 1500, slot_cap_rs: 58333.33, vix_multiplier: portfolio_simulation_only}
diagnostics_logged_not_gated: [phi_ar1_intraday, is_expiry_day, vix_level, vix_tercile, factor_used, factor_weight, beta]
trials_registry: research/evidence/trials_registry.csv
```

**`research/studies/prereg/orb_prod_v1.yaml`:**

```yaml
id: ORB_PROD_v1
definition: >
  Production live path imported read-only: track2_orb_signal_adapter logic ->
  LiquidMomentumEngine.evaluate_15m_orb_breakout (volume vs slot median >= regime multiple; 3.5x in practice
  because production passes no breadth; extension <= 0.5*ATR14; MarketRegimeFilter on the NIFTY opening range)
  -> calculate_position_size (stop = max(OR low, entry - 1.5*ATR14); SL-L offset 0.5%).
  Eligible bars 09:30-14:30; first qualifying bar per symbol-day.
exits: {two_tranche: [1.5R, 3.0R], breakeven_after_t1: entry_fill_price, policy_exit: "15:05"}
split: same as RESID_REV_v1
purpose: baseline R statistics and daily P&L series; no parameters are chosen
record: git blob hashes of every imported production file
```

---

## Appendix C: Errata and seeds

### C.1 Corrections to earlier material

1. **The 24-Sep audit's first-break mean.**
   - The prose says −0.046R, but its own probe prints −0.084R (re-run 25 Sep).
   - The −0.046R figure appears if ₹61.99 is divided by a nominal ₹1,500 instead of each trade's realised risk (median ₹832).
   - **Use −0.084R.**
2. **The 25-Sep research report (from the research run).**
   - **Null-scan rate:** its 13.5% is a continuous-time bound. Discrete 15-minute scanning gives 8.1% (23 returns) and 6.2% in the window.
   - **Absorption split:** its 60/40 is the T → ∞ limit. The split by 15:00 is 23.2% stops vs 7.3% T1.
   - **RESID_REV stop and half-life gate:** its sketch measured the stop over the remaining session and gated on a residual AR(1) half-life. This plan instead uses price σ over the holding window and only logs φ, because short intraday samples bias φ downward (Hurwicz bias).
   - **Fee rate:** it cites NSE/FA/73061 (₹306.99 + ₹0.01 IPFT per crore per side from 1 Mar 2026). This is not independently verified. Research uses 0.0030699% (₹61.99) and production uses 0.00297% (₹61.85). Verify against the NSE circular before the P8 fee patch.
   - **Pairs:** its "single-leg dominates" is conditional on μ (A.12).

### C.2 `deep_mathematical_strategy_research_20260925.md` (fbc668b) vs measured data

| Claim there | Measured or derived |
|---|---|
| ORB Tier 1; E[R] +0.43R; Sharpe 1.72; "P(T2 given T1) ≈ 0.55" | ORB −0.003R [−0.20, +0.22], n = 41. First breaks: −0.084R, n = 103; q = 0.10 |
| COMPASS Tier 1; p ≈ 52%; +0.26R | −0.095R [−0.27, +0.11], n = 17 |
| VWAP_RECLAIM Tier 2; +0.19R | −0.258R [−0.45, −0.05], n = 89; the CI is entirely below 0 |
| VOL_SQUEEZE +0.21R | −0.530R [−0.79, −0.27], n = 13 |
| Win rates, Sharpes, correlations and drawdowns for PAIR, GEX_FLIP, MAX_PAIN, PEAD, STATARB and MLOFI | These strategies have zero trades, so none of these numbers is measured |
| Ensemble DSR 0.96, "99% confidence" | Not computed from any returns. A DSR needs trades and a trial count (A.14) |
| "Loss_max = 3 × ₹1,500 = ₹4,500 … zero ruin risk" | That is risk-to-stop, not a maximum. Gap-throughs, unfilled SL-limits and band freezes can exceed it (P6.7) |
| STATARB_PAIRS as a core strategy | Conditional on μ (A.12). The pair examples lie outside the ₹75,000 Cr cap, and TATAMOTORS is now TMPV |

### C.3 Trials-registry seed (probe section F, re-run 25 Sep, trigger basis)

Each row is the first signal per stock-day. `sample_id` = `repo32_2026-08-10_2026-09-23`.

| Strategy, threshold | n | Mean net R [95% CI] |
|---|---|---|
| ORB volume 2.0× | 49 | −0.078 [−0.27, +0.11] |
| ORB volume 2.5× | 41 | −0.003 [−0.22, +0.21] |
| ORB volume 3.5× | 29 | −0.152 [−0.35, +0.05] |
| VWAP_RECLAIM volume 1.5× | 106 | −0.276 [−0.46, −0.08] |
| VWAP_RECLAIM volume 1.8× | 89 | −0.258 [−0.45, −0.05] |
| VWAP_RECLAIM volume 2.5× | 67 | −0.268 [−0.49, −0.03] |
| VOL_SQUEEZE volume 1.5× | 14 | −0.510 [−0.80, −0.20] |
| VOL_SQUEEZE volume 2.0× | 13 | −0.530 [−0.78, −0.26] |
| VOL_SQUEEZE volume 3.0× | 6 | −0.440 [−0.85, −0.06] |
| TRAPDOOR min RVOL 1.0 | 21 | −0.130 [−0.40, +0.13] |
| TRAPDOOR min RVOL 1.3 | 18 | −0.077 [−0.35, +0.19] |
| TRAPDOOR min RVOL 1.8 | 11 | −0.186 [−0.63, +0.26] |
| LAST_LIGHT min ER8 0.30 | 12 | +0.255 [−0.13, +0.70] |
| LAST_LIGHT min ER8 0.40 | 11 | +0.226 [−0.16, +0.72] |
| LAST_LIGHT min ER8 0.50 | 8 | +0.234 [−0.29, +0.86] |
| RECOIL min stretch 1.2 ATR | 7 | −0.394 [−0.90, +0.11] |
| RECOIL min stretch 1.4 ATR | 7 | −0.381 [−0.83, +0.07] |
| RECOIL min stretch 1.6 ATR | 7 | −0.388 [−0.84, +0.10] |
| COMPASS min RVOL 1.2 | 17 | −0.073 [−0.26, +0.16] |
| COMPASS min RVOL 1.5 | 17 | −0.095 [−0.27, +0.12] |
| COMPASS min RVOL 2.0 | 14 | −0.163 [−0.42, +0.09] |

---

## Appendix D: Commands (Windows)

```powershell
# P0 - from the MAIN checkout (read-only there), create the worktree; then work only inside it
cd "C:\Users\yashw\swing trades"
git status
git worktree add "C:\Users\yashw\swing-trades-track2" -b track2/decision-engine
cd "C:\Users\yashw\swing-trades-track2"
$env:TRACK2_HISTORY_DIR = "C:\Users\yashw\swing trades\shared\track2_liquid\history"
$py = "C:\Users\yashw\swing trades\.venv\Scripts\python.exe"

# tests
& $py -m pytest research\tests -q

# research-only dependencies (never edit the production requirements.txt)
& $py -c "import pyarrow" 2>$null; if ($LASTEXITCODE) { & $py -m pip install pyarrow }

# reference replay (read-only)
& $py "Claude outputs\2026-09-24_beacon_probes\probe_empirical.py"

# data (P3), resumable; credentials come from the main checkout's antigravity\config\dhan_config.json
& $py -m research.data.dhan_history --universe research\data\universe_seed.csv --from 2021-10-01 --interval 15
& $py -m research.data.nse_events --from 2021-10-01          # only after decision 7
& $py -m research.data.validate --report research\notes\p3_data_report.md

# studies (P7)
& $py -m research.studies.run_design  --prereg research\studies\prereg\resid_rev_v1.yaml
& $py -m research.studies.run_holdout --prereg research\studies\prereg\resid_rev_v1.yaml
```

*End of plan. Nothing in it authorises real-capital trading (Rule 1).*
