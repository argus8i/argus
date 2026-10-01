# Codex independent Day 4 review — CHANGES_REQUIRED

Reviewed target: `9157a8685e709e4788832265a0d7292f06c03963`, against `565d5a8`.
Workspace HEAD at review: `8c2cfc382b131f6dda85056c6a6065f38bd1408f`.
The requested Day 4 implementation, tests, runner, reports and ledgers have no differences between the target and working tree. `318a4be` only updates the dispatch prompt. No implementation or canonical evidence was changed by this review.

Verdict: **CHANGES_REQUIRED**. Day 4 acceptance and promotion to the canonical qualifying paper desk are refused. Passing helper tests do not establish correctness of the actual evidence-generating runner.

## Blocking findings

1. **P1 — False acceptance labels.** `scripts/run_walk_forward_simulation.py` report template hardcodes PASS. The committed `walk_forward_report.md` itself reports PF 0.83 against 1.30, win rate 43.3% against 45%, expectancy -0.093R against >0.250R, and drawdown 9.32% against 6%, yet labels each PASS. Regenerate verdicts from computed predicates and make acceptance fail when any required hurdle fails. Cash buffer reporting uses minimum equity rather than minimum cash. The CSV inspection found minimum cash Rs 137,542.49; the report instead presents minimum equity Rs 233,684.42 as cash.

2. **P1 — Invented locked exits and uncapped sales.** Runner lines 155–180 replace an impossible exit with TIME_STOP after the holding limit, including zero-volume locked bars. All exits sell the entire holding without calling `compute_fill_shares`; the thin-exit probe sells the full holding on a 10-share-volume session. Preserve locked/unfilled quantities and capacity, apply the aggregate session participation budget to both sides, reconcile partial fills and costs, and increment holding/locked counters once per session. The second `evaluate_bar_exit` call currently increments those counters again.

3. **P1 — Invalid entries and ignored entry-day losses.** Runner lines 213–216 use `low <= reference_price` and then fill at `max(open, reference)`. This permits a buy-stop fill when the entire bar is below its trigger, even above the session high. Existing positions are evaluated before new entries, so an entry-day stop breach is ignored. Apply the registered order type/trigger and entry-session conservative path rules. The corresponding two runner probes fail.

4. **P1 — Current eligibility bypass.** Queued entries never revalidate the execution session's universe/F&O/surveillance state. Signal metadata at lines 328–331 hardcodes F&O true, surveillance false and EQ. The probe changing eligibility to false on entry day still opens a trade. Derive current status from authoritative point-in-time data, fail closed on missing status, and apply required disqualification/exit handling to existing positions.

5. **P1 — Sealed holdout guard is disconnected.** `WalkForwardEngine.run_simulation_for_dates` contains a guard, but neither `run_fold_simulation` nor `main` calls it. A synthetic 2025 invocation succeeds without opt-in. Enforce the boundary on the actual evaluation entry point before processing data. This probe uses synthetic dates only and does not evaluate the real sealed dataset.

6. **P1 — Drawdown acceptance is incomplete.** `compute_backtest_metrics` lines 567–585 initializes peak from the first equity observation, hiding losses before that point, and excludes the 6% drawdown predicate from `hurdle_passed`. A Rs 230,000 first observation reports zero drawdown against a Rs 250,000 corpus. A profitable trade alongside an 8% equity drawdown passes the hurdle. Both probes fail. Seed peak from initial corpus and implement the stated drawdown and strict expectancy thresholds.

7. **P1 — Pooled equity is not a continuous portfolio.** Each fold starts a fresh Rs 250,000 account; line 444 concatenates their absolute equity curves. The CSV ends 2023 at Rs 245,399.83 with three open slots and Rs 81,594.76 holdings, then starts 2024 at Rs 250,000 cash with zero holdings. Six trades are labelled UNRESOLVED, but their MTM values/as-of dates are absent from trades.csv. Independent fold experiments are permissible when labelled; this concatenation cannot substantiate a continuous pooled portfolio drawdown or carried-position claim. Report independent fold metrics separately or implement a reconciled continuous ledger with unresolved inventory and explicit boundary treatment.

8. **P2 — Friction repricing is not from an unadjusted fill ledger.** `compute_ledger_net_pnl` uses entry/exit prices already slipped under Tier 2. Tier 1 therefore retains Tier 2 slippage, and Tier 3 adds severe normal slippage on top of realistic slippage. It ignores gap-specific rates and applies DP per trade rather than per symbol/sell day. Monotonic numerical ordering does not validate tier calibration. Store raw execution bases and fill reasons; reprice each policy exactly once with grouped DP and itemized costs.

9. **P1 — Stress acceptance is overstated.** The stress report is a hardcoded string rather than computed scenario output. Its 6.09% lockout result does not meet the stated <=6% gate; substituting 'bounded to 1 slot' is not approval of a changed criterion. The LC fixture directly creates Rs 1,900 initial risk (380 shares times Rs 5), above the Rs 1,500 Track 2 budget, uses Track 1 CROPSTER calibration, and bypasses runner time-stop behavior. Election tests close trades without statutory costs; the bear test directly assigns eight Rs 1,550 losses. These are synthetic component probes, not verified historical regime runs through the shared governor. Generate scenario reports from the actual runner, keep Track 1 calibration explicitly separate, and do not claim equity proves an unencumbered cash reserve.

## Additional architecture limits

The runner constructs fold metadata but does not use `PurgedFoldManager.filter_training_trades` or validate the declared purge against the actual session calendar. There is no training/tuning pipeline here; do not claim demonstrated purging of a fitted training ledger. `BacktestSimulation.arbitrate_signals` performs priority/slot selection only, while its `open_positions` remains empty in the runner. The runner duplicates sizing/cash checks instead of integrating the governor's portfolio state. Review those contracts with the orchestrator before canonical promotion. Sleeve B checks row count, not independently validated unique historical sessions.

## Reproduced checks and evidence

All commands ran with working directory `C:\Users\yashw\swing trades`.

- Baseline: `.venv\Scripts\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py tests/test_day4_backtest.py --basetemp=C:\Users\yashw\swing trades\shared\trust\artifacts\CODEX-DAY4-9157A86-independent-ca36dcce-tmp -v -p no:cacheprovider` (the path argument was passed as one subprocess argument). **114 passed in 0.63s; exit 0.** Exact executable path/quoting and raw stdout/stderr: `artifacts/CODEX-DAY4-9157A86-independent-ca36dcce-suite.log`.
- Adversarial regression probes: `.venv\Scripts\python.exe -m pytest shared/trust/artifacts/test_codex_day4_9157a86_review.py -v -p no:cacheprovider`. **8 failed in 3.66s; exit 1.** Raw stdout/stderr: `artifacts/CODEX-DAY4-9157A86-independent-ca36dcce-probes.log`. This pre-existing untracked probe file was inspected and reused without modification; it formalizes the eight runner/metric defects before any fix.
- Both fresh logs have adjacent `.log.sha256` files.
- Submitted `DAY4-BACKTEST-STRESS-TESTS.log` SHA-256 reproduced exactly: `61E1547BFFB43E80F4735CFFF753FF748151C6720C5D16C46D19D0C43548B662`.
- Inspection of rounded CSV data: 141 closed trades, six unresolved, summed net PnL Rs -15,539.80, PF 0.8277945786730702, win rate 0.4326241134751773, mean stored R -0.09317021276595744, and concatenated-series drawdown Rs 23,305.17. The five-paise difference from report PnL is consistent with stored per-trade rounding; it is not the acceptance defect.

No historical backtest regeneration was run because it would overwrite the submitted reports/ledgers. No strategy promotion, merge, broker access, live order, or paper-gate change occurred. Unrelated working-tree files were preserved.

Changed/created by this review: this report; two uniquely named raw logs and their SHA-256 sidecars; pytest temporary files under the uniquely named project artifact directory. Implementation remains unchanged. Resolve the failing regressions, add acceptance/report and repricing regression coverage, regenerate evidence, and request independent review of the replacement commit.
