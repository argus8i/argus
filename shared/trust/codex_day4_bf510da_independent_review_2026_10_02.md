# Codex independent Day 4 review: CHANGES_REQUIRED

Target and verified HEAD: `bf510da1aa9835ae100d32f96aace67e4b63ce27`, branch `feature/day4-backtest-and-stress-testing`; base `565d5a8`. Prior review: `codex_day4_90255e7_independent_review_2026_10_02.md`.

**Verdict: CHANGES_REQUIRED.** The submitted 133-test suite passes, including all four 90255e7 regressions. Four additional independent acceptance probes fail. No canonical promotion or qualifying paper-evidence approval is granted. This is an engineering verdict, independent of the strategy's reported negative historical results.

## Blocking findings

1. **P1: A time stop reached during a locked session is not persisted.** `scripts/run_walk_forward_simulation.py:205` only creates TIME_STOP when positive volume and an unlocked bar permit immediate execution. The locked-bar branch in `antigravity/engine/backtest_engine.py:465` persists price stops but not the runner's expired holding limit. The independent EXPIRY_RELIEF fixture reaches holding session 5 on zero-volume 2023-02-14 above its price stop: its unresolved boundary record has `pending_exit_reason=None`. On liquid recovery 2023-02-15 the runner sells at raw close Rs 104 rather than attempting the already-due exit at raw open Rs 103. Persist mandatory time-exit intent when the holding limit expires, regardless of current executable capacity, and use the existing pending-exit open liquidation path on recovery. Both time-stop regressions fail. This completes the previous review's explicit request to persist mandatory stop/time-exit intent; it does not request a fill during the lock.

2. **P2: Partially missing or nonfinite cash observations still pass open.** `antigravity/engine/backtest_engine.py:711-713` uses `any(hasattr(...))`, filters out points without cash, and does not validate finiteness. A profitable ledger with Rs 250,000 cash on its first point and NaN cash on its second passes the hurdle. A second point lacking cash also passes. The missing-entire-series fix and pooled predicate fix are accepted, but incomplete series are not valid evidence of cash-buffer preservation. Require every supplied observation to contain valid finite cash and equity, and fail closed when an observation is missing or invalid. The two cash regressions fail. These are reusable acceptance-contract defects; no claim is made that the committed historical CSV contains these malformed observations.

3. **P2: Verification footer remains hardcoded success text.** `scripts/run_walk_forward_simulation.py:910` unconditionally emits `133 passed ... (Exit code: 0)` without running pytest or reading/verifying the sealed suite result. The committed footer now matches the actual submitted suite, but changing the constant from 129 to 133 does not implement the prior request for a footer derived from recorded execution. Derive count/status from validated execution evidence, or restrict the generator to a reproduction command and remove the unconditional success assertion. This is directly established by source inspection; the generator was not run against a fabricated failing log and the submitted reports were preserved.

## Accepted remediation and evidence

- The previous locked price-stop, partial-fill repricing, wholly absent cash series, and pooled cash-failure probes all pass. The thin-exit fixture now reconciles under identical realistic policy to the cent.
- Exported inventory reconciles for all 145 trades: `initial_shares = residual_shares + total_sold_shares`. Each trade's fill count and sold quantity reconcile to the dedicated 140-row fill ledger. There are 140 closed trades and 5 unresolved trades. This accepts the expanded evidence schema; the submitted historical fill ledger does not itself exercise multi-day partial exits.
- Closed net PnL from the submitted CSV is Rs -13,382.85; observed minimum cash is Rs 136,429.82. The strategy report correctly fails its historical hurdle.
- Component execution reproduces election loss Rs 4,675.10 / minimum cash Rs 138,325.735, bear loss Rs 12,999.48 / minimum cash Rs 208,567.345, and Track 1 calibration loss Rs 15,323.01 / 6.129204%. These are synthetic component diagnostics.
- Report and generator capability claims now explicitly exclude the live shared PortfolioRiskGovernor reservation lifecycle and dynamic sector-concentration controls. That portion of the prior finding is accepted.
- Submitted suite log: 14,113 bytes; SHA-256 independently matches the request and sidecar: `256852C13828C8B4DF454DE628AF6AB275757E1DC616B7EEC0F12544912757BC`.
- Requested implementation, report, CSV, and prior-probe files have no target-to-worktree differences. Existing unrelated changes were preserved. Base-to-target changes outside scope are not certified. No fitted-model leakage protection, actual calendar purge verification, historical full-run regeneration, live governor acceptance, or prospective qualification is newly certified.

## Reproduction and actual changes

All commands and recorder subprocesses explicitly use cwd `C:\Users\yashw\swing trades`. Reproduce with:

`.venv\Scripts\python.exe shared/trust/artifacts/run_codex_day4_bf510da_review.py`

The recorder executes the eight submitted suite files with `-v -p no:cacheprovider` and a fresh project-local `--basetemp`, then the new regressions, then component/CSV/hash inspection. Exact argv, cwd, subprocess exit codes, and raw unedited stdout/stderr bytes are retained in separate logs. Recorder exit 0 means evidence capture completed; the failing probe subprocess has exit 1.

| Check | Actual result | Artifact under shared/trust/artifacts | SHA-256 |
| --- | --- | --- | --- |
| Submitted suite and previous probes | 133 passed in 2.66s; pytest exit 0 | CODEX-DAY4-BF510DA-suite.log | 24BF71435BF8AEA9BAF488A44D79F8E56C470BDAAF3136BC66CB825DE9667AED |
| New independent probes | 4 failed in 1.11s; pytest exit 1 | CODEX-DAY4-BF510DA-probes.log | A5A900DA25D37CB21B458D82FF86BD8587150C35B9D6E5E2148614026D20FE33 |
| Component/CSV/hash inspection | subprocess exit 0 | CODEX-DAY4-BF510DA-inspection.log | See adjacent SHA-256 sidecar |

Created by this review: this report; `artifacts/test_codex_day4_bf510da_review.py`; `artifacts/run_codex_day4_bf510da_review.py`; three raw evidence logs and their SHA-256 sidecars; `artifacts/CODEX-DAY4-BF510DA-recorder-console.log`; fresh project-local pytest temporary directories. No implementation fixes, merges, broker access, orders, or trading-gate changes occurred. The four failing tests formalize the execution and cash defects before future fixes as required by the test-first gate.
