# Codex independent Day 4 review: CHANGES_REQUIRED

Target and verified HEAD: `90255e71c12fd32631fa3f6f2e42906bb0d03154`, branch `feature/day4-backtest-and-stress-testing`; base `565d5a8`.
Prior review: `codex_day4_7c23f6c_independent_review_2026_10_02.md`.

**Verdict: CHANGES_REQUIRED.** The submitted 129-test suite passes, but four independent acceptance probes fail. No canonical promotion or qualifying paper-evidence approval is granted. This verdict concerns engineering defects, not simply the negative historical strategy result.

## Blocking findings

1. **P1: Stop intent is still forgotten when the triggering session is locked.** `antigravity/engine/backtest_engine.py:423` returns before checking stop conditions and does not set pending exit intent. The runner's new pending mechanism only operates when intent was already recorded. In the independent fixture, a held security encounters a zero-volume bar at Rs 90 below its Rs 95 stop on 2023-02-08, recovers to liquid two-sided trading on 2023-02-09, and exits only on 2023-02-14. Persist triggered mandatory stop/time-exit intent even when execution is impossible; attempt residual liquidation on the first subsequently executable session. The new locked-stop regression fails. This does not request an invented fill on the locked bar.

2. **P1: Partial execution destroys the paired friction ledger contract.** `record_partial_exit` retains aggregate slipped proceeds and costs but no per-fill raw price, date, quantity or slippage classification. Runner line 246 sets `raw_exit_price` to the final fill's raw price; `compute_ledger_net_pnl` at lines 513-541 reprices the entire initial quantity at that one price and charges DP for only the final date. Independent thin-exit simulation realizes net PnL **Rs -1,064.72**, while repricing that same trade under the identical realistic policy yields **Rs -177.32**. Preserve each exit leg and reprice its actual raw level/date/quantity, with per-symbol/sell-day DP grouping and correct gap/normal treatment. Verify identical-policy reconciliation before claiming multi-tier monotonicity. The failed probe demonstrates this arithmetic defect; it does not establish that the submitted historical run contains multi-day partial exits.

3. **P2: Cash acceptance remains inconsistent and missing observations pass open.** Engine line 637 substitutes `corpus_rs` when there is no equity curve, permitting a profitable trade ledger with no cash observations to pass. Runner lines 604-609 overwrite `pooled_metrics.hurdle_passed` using a predicate that still omits cash. The exact runner predicate, extracted and evaluated by the independent probe, turns the engine's rejection of Rs 100,000 observed cash into acceptance. Both probes fail. Use a unified predicate, require nonempty valid cash/equity observations, and preserve the cash condition when replacing pooled drawdown with worst-fold drawdown. The submitted CSV's observed minimum cash passes; this is a reusable acceptance-contract defect.

4. **P2: Exported trade evidence loses original quantities and execution history.** `BacktestTrade.close` resets `shares` to zero, and runner lines 635-657 exports only that residual quantity without `initial_shares`, `total_sold_shares`, per-fill records or pending exit intent. The committed CSV has **140 CLOSED rows, all with shares = 0**, and 5 unresolved rows. Consumers cannot directly audit original executed quantities, partial sales, participation or pending exits from this ledger. Export original and residual inventory separately and include a fill ledger plus unresolved mandatory intent. The inspection artifact records the actual schema and counts; this is a data-contract finding, not a claimed fifth regression failure.

5. **P1: The previous governor claim finding is only partially remediated.** Module capability text and arbitration docstring correctly acknowledge standalone slot filtering without the live governor lifecycle or sector controls. However, `stress_test_report.md` section 1 and its generator still claim to assess risk governor stability. Component scenarios do not invoke that lifecycle, and the submitted walk-forward report does not identify the absent sector/reservation controls. Narrow all report/generator acceptance claims consistently to fixed-strategy/component diagnostics and state the excluded controls, or integrate and verify those controls. The committed stress footer also remains at 125 tests while the generator and submitted sealed suite say 129; regenerate the evidence footer from actual recorded execution rather than hardcoded success text.

## Accepted remediation and review limits

- All four previous 7c23f6c probes now pass. Capacity-blocked entry-stop and disqualification intent persists across recovery, available partial liquidation increases cash, and the engine rejects an explicitly observed cash breach.
- New partial-sale arithmetic correctly credits modeled proceeds and retains residual holdings in the tested runner path; the remaining repricing/evidence defects are identified above.
- The submitted log is 13,582 bytes; SHA-256 independently matches the request and sidecar: `741323A6C034BA2247F8576A2085A001948E295558D7B398FFDA1F9492D0CF37`.
- Component execution reproduces election loss Rs 4,675.10, election minimum cash Rs 138,325.735, bear loss Rs 12,999.48, bear minimum cash Rs 208,567.345, and Track 1 calibration loss Rs 15,323.01 / 6.129204%. These are synthetic component results, not historical governor acceptance or Track 2 qualification.
- Submitted CSV inspection reproduces closed net PnL Rs -13,382.85 and minimum cash Rs 136,429.82. The report honestly fails the strategy hurdle. Full historical report regeneration was not performed; submitted reports/CSVs were preserved.
- Scope and supporting Day 1-3 test files have no target-to-worktree differences. Existing unrelated changes were preserved. Base-to-target changes outside the requested scope are not certified by this review. No fitted-model leakage protection or actual session-calendar purge validation is newly certified.

## Reproduction and actual edits

Every shell command explicitly used cwd `C:\Users\yashw\swing trades`; recorder subprocesses use that same absolute cwd. No implementation fixes, broker access, orders, holdout evaluation, merges or gate changes occurred.

Run `.venv\Scripts\python.exe shared/trust/artifacts/run_codex_day4_90255e7_review.py` for the suite and independent probes, or append `--inspect` for component/CSV/hash inspection. The suite uses the seven test files in the request, with `-v -p no:cacheprovider` and a fresh project-local `--basetemp` to avoid inaccessible existing temporary directories. Exact argv, cwd, subprocess exit codes and raw unedited stdout/stderr bytes are recorded in each log.

| Check | Actual result | Artifact under shared/trust/artifacts | SHA-256 |
| --- | --- | --- | --- |
| Submitted suite and prior reviewer probes | 129 passed in 7.75s; pytest exit 0 | CODEX-DAY4-90255E7-suite.log | DCD4FAA7F43EEFE7FA24CBF41CD9FF45A8132ECD87426D615AD614C2ED6FB006 |
| New independent probes | 4 failed in 2.46s; pytest exit 1 | CODEX-DAY4-90255E7-probes.log | F5681B56BA2837E7F3ED3F2110923A1CFBB9FCA4F678A4AE427E08B542E29E01 |
| Component/CSV/hash inspection | subprocess exit 0 | CODEX-DAY4-90255E7-inspection.log | 584F2784D7B227C1E1A752E4496E1D172F61953CE7BDCD165D726AA52A5C31C7 |

The first recorder run saved both test logs and seals, then its console printing encountered a cp1252 UnicodeEncodeError. The raw test output and recorded pytest exit codes were unaffected. The recorder was corrected to emit UTF-8; its subsequent inspection invocation completed with exit 0. Recorder completion is distinct from pytest success.

Created by this review: this report; `artifacts/test_codex_day4_90255e7_review.py`; `artifacts/run_codex_day4_90255e7_review.py`; three logs and their three SHA-256 sidecars; fresh project-local pytest temporary directories. The four failing probes formalize the defects before any future implementation fixes, as required by the test-first gate.
