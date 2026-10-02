# Codex independent Day 4 review: CHANGES_REQUIRED

Target: `ee58cb300c1def019b487ab03266f80e61c46f7a`; base: `565d5a8`.
Workspace HEAD: `7d2cbaf87a116b8e9c6d8b0efeaf1044fb41571d` (dispatch update).
The requested scoped files have no working-tree differences from the target. This is a review-only result; canonical promotion and qualification acceptance are refused.

## Blocking findings

1. **P1: Aggregate participation is still bypassed.** `scripts/run_walk_forward_simulation.py:256` calls `compute_fill_shares` without accumulated session usage. The entry-session stop path at line 291 immediately sells all purchased shares without checking remaining participation capacity. The independent roundtrip regression buys 150 and sells 150 on a 1,000-share session: 300 shares versus a 150-share cap. Existing-position exits at lines 172–176 reject every partial sale rather than reconciling available fills and retaining the remainder. Track symbol/session usage across entry and exit paths, reconcile partial fills, costs and residual inventory, and preserve unfilled exit requests. Passing the previous thin-exit test proves only that the whole holding is not sold on the thin bar.

2. **P1: Held-position eligibility is ignored.** The new line 212 check protects queued entries only. Existing positions never consult current universe eligibility or initiate disqualification exits. The regression revokes eligibility on 2023-02-08 with ample liquidity; the runner holds until its 2023-02-14 time stop. Add current-session disqualification handling to held inventory, including pending/unfilled exit state. The synthetic probe is not a real surveillance event; it tests the same authoritative eligibility contract used by the entry path.

3. **P2: Acceptance predicates still disagree.** `antigravity/engine/backtest_engine.py:608` uses `net_expectancy_r >= 0.25`; runner line 588 and the declared hurdle require `> 0.250`. A profitable synthetic ledger with exactly 0.25R passes the engine gate. Use one acceptance contract for metrics and reports, including the required cash predicate; test boundary values.

4. **P1: Stress claims remain broader than their evidence.** Runner lines 715–718 hardcode election/bear/cash PASS and LC FAIL despite interpolating computed numbers. `run_adversarial_stress_scenarios` directly constructs positions and bars; it does not exercise `run_fold_simulation`, historical signal generation, point-in-time eligibility or shared governor allocation. The bear loop directly constructs CLOSED trades and increases/decreases cash only by net PnL, without debiting entry notional while inventory is held. Thus `bear_min_cash` is ending cash, not minimum liquid cash across all observations. Election cash is not tracked or included in the reported reserve minimum. Entry slippage is omitted from these scenario trades. Computed statutory costs and the honest LC FAIL improve the report, but do not establish the stated historical-regime/governor acceptance. Label these fixtures synthetic component scenarios, isolate the Track 1 calibration artifact, generate verdicts from predicates, and either run the actual portfolio execution path or narrow the acceptance claims. The 29-test footer is also a fixed template, not a generated result.

5. **P1: Purged evaluation/shared-governor claims are not integrated.** The report still says information boundaries are strictly enforced via `PurgedFoldManager`, while the runner only constructs fold descriptors and never invokes its training-trade filter or validates the embargo against the actual session calendar. There is no fitted training/tuning pipeline demonstrated here. `BacktestSimulation.arbitrate_signals` only deduplicates and counts slots; its `open_positions` is not synchronized with the runner's `open_trades`. Runner sizing duplicates a subset of checks and never calls the shared governor's allocation/lifecycle interface (including sector concentration). Integrate the reviewed contracts and add runner-level regressions, or accurately limit the deliverable to fixed-strategy historical diagnostics without claims of verified purging/shared-governor acceptance.

6. **P2: Independent folds still share a drawdown peak.** Lines 496 and 507 concatenate reset fold equity and compute one drawdown across it. CSV partitioning and Section 2.1 correctly describe independent accounts, but do not reset the peak in this computation. An earlier fold's peak can inflate the next fold's drawdown. Aggregate closed-trade statistics separately and define aggregate drawdown as worst independent-fold drawdown, or provide a reconciled continuous portfolio. Do not let cross-fold reset arithmetic determine qualification.

## Remediation acknowledged

All eight prior reviewer probes now pass. The runner checks the buy-stop trigger, handles entry-session stop breaches, refuses zero-volume time-stop liquidation, checks entry-day eligibility and guards sealed holdout evaluation. Metrics include initial corpus losses and a drawdown predicate. The walk-forward table now reports failed hurdles honestly and computes minimum daily cash. Raw price bases and gap-specific repricing plus per-symbol/sell-day DP grouping are implemented. Unresolved trade MTM/as-of fields and fold identifiers are present. These improvements do not resolve the above integration defects. A negative strategy result alone is not the reason for this engineering verdict.

## Reproduction evidence

Every command ran with explicit cwd `C:\Users\yashw\swing trades`. No real holdout evaluation, historical report regeneration, merge, broker access, order submission or gate change occurred.

Execute `.venv\Scripts\python.exe shared/trust/artifacts/run_codex_day4_ee58cb3_review.py` to reproduce both runs. The recorder passes exact subprocess argv with an absolute project cwd, writes captured stdout/stderr bytes without editing them and records each pytest exit code. Its own exit 0 means recording completed, not that probes passed.

- Submitted suite plus prior probes: **122 passed in 4.65s; pytest exit 0**. Exact command argv and raw output: `artifacts/CODEX-DAY4-EE58CB3-suite.log`; SHA-256 `DDEBFA447D774D75AEB4AA5D6EEA99DA3F01CE7A9CDBCD6F374778920F65F181`.
- New independent regressions: **3 failed in 1.30s; pytest exit 1**. Exact command argv and raw output: `artifacts/CODEX-DAY4-EE58CB3-probes.log`; SHA-256 `69711E66CC260F79FF0A15B0874463DAC94B1AC751BAA99D9F3ADB14314042F8`.
- Submitted `DAY4-BACKTEST-STRESS-TESTS.log` hash reproduced: `F0554AFF1E8DD6D09D7A43CF9F0FB6C2F6750BB84780AC770221FAB3CC9571E1`.

New regressions formalize the three executable defects before fixes, per Rule 8's test-first gate. Other findings are source-contract inspection findings, not claims of additional failed commands.

Created by this review: this report; `artifacts/test_codex_day4_ee58cb3_review.py`; `artifacts/run_codex_day4_ee58cb3_review.py`; two raw logs and adjacent SHA-256 sidecars; pytest temporary directories under the project artifact directory. Implementation, submitted evidence, prior reviewer probes and unrelated work were preserved.
