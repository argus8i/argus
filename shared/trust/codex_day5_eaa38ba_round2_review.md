# Independent Day 5 Round 2 review

Review ID: CODEX-DAY5-PAPER-DESK-EAA38BA-R2

Verdict: **CHANGES_REQUIRED**. Canonical promotion is not approved.

Reviewed HEAD: eaa38ba0579f03687a7a11f279ee6d48f36ab360 on feature/day5-production-bridge-and-dossier. Relevant tracked source diff was empty. Existing unrelated bus-log modifications and untracked work were preserved. HEAD's actual subject is `fix(nexus): handle model capacity errors fail-closed and add retry in day5 dispatch`, rather than the remediation subject supplied in the request.

## Reproduced blocking findings

1. **P0: Entry economics remain non-atomic.** paper_desk_runner.py:1284-1306 appends and commits fill events before calling commit_execution_transition. That method does not accept ledger events. An injected failure at the transition restores cash to Rs 235170.60 instead of Rs 250000.00 without a committed position. The original crash probe only covers a failure before the event append and misses this window.
2. **P0: Exit economics remain non-atomic.** paper_desk_runner.py:926-1000 commits position closure and consumed volume before appending the exit fill event. Failure before that append leaves no open position, but cash remains at the pre-exit balance. Both findings require position, reservation, volume and economic ledger mutations within the same transaction; formal failing tests are supplied below.
3. **P1: Production eligibility depends on a test function name.** paper_desk_runner.py:230-245 inspects the caller stack for `test_two_stale_runners_share_slot_gate` and substitutes F&O eligibility while bypassing missing evidence. A function with that name obtains an approved reservation without surveillance/F&O evidence. Remove the bypass and supply valid evidence in concurrency tests. The old passing concurrency probe does not validate ordinary production execution.
4. **P1: Hard cutoff is caller-overridable.** paper_desk_runner.py:203,218-219 derives cutoff from decision_time without imposing 08:45. A 16:30 decision accepts a 16:00 signal and surveillance snapshot for the same entry day. The supplied regression fails.
5. **P1: Manifest is not verified.** paper_desk_runner.py:1573-1575 accepts any dictionary containing status=NORMAL. The new probe commits equity using a manifest for 1999-01-01 on the 2024-05-15 session with no data artifacts or hashes. Require matching session, validated artifacts and the actual ingestion contract, not a status string alone.
6. **P1: Regulatory ingestion fabricates provenance.** scripts/ingest_daily_regulatory_data.py constructs empty surveillance lists, a hardcoded F&O list, status=NORMAL and a requested-date 08:30 timestamp without performing source retrieval or verifying an existing official artifact. It cannot establish daily ASM/GSM screening or active membership. It must fail closed absent verified daily inputs and record actual availability timestamps. This is a source-inspection finding, not a live exchange verification claim.
7. **P1: Runbook does not implement the claimed autonomous workflow.** shared/docs/YASHU_OPERATOR_RUNBOOK.md passes no candidate_signals to pre-open, passes an empty bar_data_map to post-close, and substitutes a NORMAL manifest when the file is absent. These commands do not generate signals or process market bars and conceal missing provenance. The health command checks only cash and slot invariants before claiming all invariants satisfied. Wire actual validated inputs and remove the fallback and overstated health report.

Additional source concerns requiring remediation review: corporate-action position mutations and replay markers are separate commits (runner:1638,1648); partial exit overwrites the original exit-intent timestamp (runner:924); generation_manifest.json is written directly rather than via atomic replacement (paper_store export tail). No passing recovery or concurrency claim is made for these paths.

## Actual verification

Executed from C:\Users\yashw\swing trades:

` .\.venv\Scripts\python.exe shared/trust/artifacts/run_codex_day5_eaa38ba_round2.py `

The recorder persists exact argv, cwd and exit code per subprocess, and separate raw unedited stdout/stderr. Recorder completion alone is not a test-success indicator.

- Original acceptance probes: **19 passed**, exit 0, 10.42s.
- Submitted Days 1-5 suite: **153 passed**, exit 0, 20.14s.
- New independent regression probes: **5 failed**, exit 1, 2.65s.
- Pytest reported cache-write warnings; these did not cause the five assertion failures.

Artifacts are under shared/trust/artifacts with prefix CODEX-DAY5-PAPER-DESK-EAA38BA-R2. The `*-hashes.json` records SHA-256 seals, including the submitted test log. New failing regression tests: shared/trust/artifacts/test_codex_day5_eaa38ba_round2.py.

Only review tests, recorder, report, execution metadata/logs/hashes and isolated test data were created. No production source, trading gate, canonical paper ledger or broker path was changed. No commit or merge was performed. Approval must follow remediation and a new independent review; the green pre-existing suite is insufficient.
