# Codex independent Day 4 review: CHANGES_REQUIRED

Target: `7c23f6c1cd023e3e5cd67a13f44f7438beb32373`; base: `565d5a8`.
Workspace HEAD during review: `676f63effb601e2f49592d41295a3387ec2ccec3`, on `feature/day4-backtest-and-stress-testing`.
The only committed target-to-HEAD difference is `scripts/dispatch_day4_codex_review.py`. All requested scoped implementation, tests, reports and submitted evidence match the target; tested supporting Day 1-3 contracts also have no target-to-worktree differences. Existing unrelated changes were preserved.

**Verdict: CHANGES_REQUIRED.** This is an engineering acceptance refusal, not a rejection merely because strategy profitability is negative. No canonical promotion or qualifying-evidence approval is granted.

## Blocking findings

1. **P1: Unfilled mandatory exits are forgotten after recovery.** `scripts/run_walk_forward_simulation.py:191` discards an exit event when capacity cannot cover the entire holding. The entry-session stop path at lines 315-347 likewise stores an ordinary open trade without pending exit intent. On subsequent sessions, exits are reevaluated from scratch. Independent probes demonstrate that a blocked entry stop on 2023-02-07 exits only on 2023-02-14, despite ample capacity on 2023-02-08. A disqualification on 2023-02-08 with insufficient full-exit capacity is forgotten when eligibility returns on 2023-02-09; liquidation occurs on 2023-02-15. Persist mandatory exit intent and attempt the remaining inventory at the earliest subsequently executable price, without assuming the old stop price is available. Record the attempted/unfilled exit and reconcile holdings and cash. Two failing regression tests formalize these defects.

2. **P1: Partial exits remain unimplemented.** Runner lines 190-193 refuse every sale unless the entire position fits in the remaining session cap. In the independent thin-exit fixture, 295 held shares encounter a 1,000-share session: 150 shares of modeled exit capacity are available, but cash remains exactly Rs 220,442.855 before and after the session. No inventory is reduced. The previous review explicitly requested partial-sale reconciliation and residual inventory; the new participation counter fixes the upper bound but does not complete that requirement. Execute conservatively supported partial quantities, allocate costs without duplication, retain residual inventory and pending mandatory exits, and aggregate buys/sells against the same session budget. The failed partial-exit probe demonstrates the remaining behavior.

3. **P2: The acceptance contracts still disagree on the cash predicate.** `antigravity/engine/backtest_engine.py:605` and runner lines 552-557 omit cash from `hurdle_passed`; the report gate at lines 632-645 includes it. An independent ledger with 1R positive expectancy, no drawdown, and Rs 100,000 cash is accepted by the engine despite the Rs 136,000 requirement. Unify the acceptance contract and fail closed for missing or breached cash observations. The submitted historical report itself correctly reports its observed cash floor; this finding concerns the reusable gate, not a claim that this historical ledger breached cash.

4. **P1: Shared-governor acceptance remains claimed without integration.** Synchronizing `sim.open_positions` fixes the slot-count divergence, but `BacktestSimulation.arbitrate_signals` (lines 296-358) still only deduplicates/ranks candidates and compares their count against `risk_governor.max_slots`. It never invokes governor allocation, reservation or fill reconciliation. Runner sizing independently duplicates selected scalar limits and never supplies sector information to the governor, leaving the reviewed sector-concentration and unmapped-sector controls outside this execution path. The module's core-capabilities claim of shared PortfolioRiskGovernor integration, arbitration docstring claim of sizing/allocation, and stress report claim of assessing risk-governor stability remain broader than the implementation. Integrate and adversarially verify the reviewed governor lifecycle, or explicitly narrow all acceptance claims to standalone fixed-strategy/component diagnostics and identify the absent controls. This is a source-contract finding; no additional failing governor test is claimed here.

## Remediation accepted and limits

- Aggregate per-symbol/session volume accounting is present on both entry and full-exit paths; all 11 previous reviewer probes pass.
- Current-session eligibility revocation now triggers an immediate full-exit attempt when enough liquidity exists. Persistence and partial liquidation remain unresolved as above.
- Net expectancy now uses strict `> 0.250`; the boundary probe passes.
- Stress fixtures are now labeled synthetic component scenarios, apply entry slippage and inventory notional debits, and generate summary verdicts from computed predicates. Independent component execution reproduces election minimum cash Rs 138,484.395, bear minimum cash Rs 208,567.345, LC cash Rs 211,926.39, and LC loss Rs 15,323.01 / 6.129204%. These establish component arithmetic, not historical portfolio replay or governor acceptance. The Track 1 fixture is explicitly labeled; it is not Track 2 qualifying evidence.
- Section 2 now accurately identifies fixed-parameter diagnostics rather than a fitted/tuned training pipeline. Fold descriptors are still not a demonstrated session-calendar purge validator; no trained-model leakage protection is certified by this review.
- Pooled drawdown now uses the worst independent-fold drawdown, eliminating the prior shared-peak bug. Reading the rounded CSV reproduces Rs 11,867.01 and Rs 15,891.78, versus reported Rs 15,891.79 from unrounded arithmetic. That one-paisa difference is consistent with CSV rounding and is not a blocker.
- Submitted CSVs contain 140 closed and 5 unresolved trades; summed rounded closed net PnL is Rs -13,382.85 and minimum recorded cash is Rs 136,429.82. Reports honestly fail the strategy hurdle. Full historical report regeneration was not performed.
- The stress-report verification footer still hardcodes a test count/result; component report generation does not execute pytest. The independently verified suite result below supports the current snapshot only.

## Reproduction evidence

Every shell command used explicit cwd `C:\Users\yashw\swing trades`; recorder subprocesses use the same absolute cwd. No broker access, orders, holdout evaluation, merge, implementation fix, report overwrite, gate change or destructive Git operation occurred.

Run `.venv\Scripts\python.exe shared/trust/artifacts/run_codex_day4_7c23f6c_review.py` for the submitted suite and new probes; use `--retry` for the suite with a fresh project-local pytest base temporary directory, and `--inspect` for component execution/CSV arithmetic. Each log includes exact argv, cwd and subprocess exit code, followed by stdout/stderr captured as unedited bytes. Recorder exit 0 means recording completed, not that the tests passed.

| Check | Actual result | Raw artifact | SHA-256 |
| --- | --- | --- | --- |
| Initial submitted suite + previous probes | 122 passed, 3 fixture errors; pytest exit 1. Existing `.pytest_tmp` was inaccessible. | `artifacts/CODEX-DAY4-7C23F6C-suite.log` | `7244A4D641AA27AFE8CAFED670E21964B2A70B3AA264A00B22A3DE843A7F83DE` |
| Fresh project-local temporary directory retry | 125 passed in 3.85s; pytest exit 0 | `artifacts/CODEX-DAY4-7C23F6C-suite-retry.log` | `FDC06055B36C0506487E8BCBB38B589D35DCEA3780BABE7D1D0A8A3FCBCC4143` |
| New independent acceptance probes | 4 failed in 1.21s; pytest exit 1 | `artifacts/CODEX-DAY4-7C23F6C-probes.log` | `C1091CBDA639E8A3980D9C7789547447EA9B54AB4AFB64385E14E64A8E1C75CB` |
| Synthetic component execution and CSV arithmetic | exit 0 | `artifacts/CODEX-DAY4-7C23F6C-inspection.log` | `A57E350F2741AE7C830F243CF4E78F346947C54011AA401B30465CD5DCBBCDAB` |

Submitted log hash independently reproduced: `A3F698DC34C06DA4D7F09CB2603EE07BB257CB65C2C40135C8477F02D4EC383B`, matching its sidecar and review request.

Created by this review: this report; `artifacts/test_codex_day4_7c23f6c_review.py`; `artifacts/run_codex_day4_7c23f6c_review.py`; four raw logs and four SHA-256 sidecars; a fresh project-local pytest temporary directory. Submitted implementation and evidence were preserved. New failing probes satisfy the test-first formalization requirement before any future fixes.
