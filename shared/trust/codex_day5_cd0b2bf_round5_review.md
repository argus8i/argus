# Independent Round 5 review

**Verdict: CHANGES_REQUIRED**  
**Review ID: CODEX-DAY5-PAPER-DESK-CD0B2BF-R5**  
Reviewed commit: `cd0b2bf5063f04a827d92c2218584be6b73a5f28`, branch `feature/day5-production-bridge-and-dossier`, requested base `8d144ff`.

The previous probes all pass, but the broader Round 4 requirements are not fully remediated. Approval cannot be issued on this evidence.

## Actual execution

| Check | Result | Exit code |
|---|---|---|
| Combined submitted suite and previous independent/runbook probes | 199 passed in 34.12s | 0 |
| New Round 5 acceptance probes | 9 failed in 2.69s | 1 |
| Start/end commit identity | Both equal requested HEAD | 0 |
| Start/end reviewed source diff against HEAD | Empty | 0 |

Submitted test log SHA-256 independently matches `58566bd9852c9d2884c5e26dca4476e04f9ed975eafa0779e9e8063650d266ff`. The wrapper itself exits 0 after recording child failures; the pytest exit codes above are the acceptance results.

## Blocking findings

1. **P1: Existing arbitrary bytes still verify absent market data** (`antigravity/paper/paper_desk_runner.py:1607`). An unrelated text file with its matching SHA-256, a NORMAL label and session label seals equity even with an empty bar map. File existence and byte integrity do not validate bhavcopy schema, source session or consumed market-data coverage. A nonempty bar map is also accepted by a boolean check without coverage validation (inspection). Regression: `test_arbitrary_existing_file_cannot_verify_empty_market_data`. Require a validated market-data artifact and bind it to the actual consumed bars/session, including retry; do not treat arbitrary files or dictionary cardinality as verification.

2. **P1: Default candidate producer still reports a successful screen without input or screening** (`scripts/generate_candidate_signals.py:88`). With neither upstream source nor existing output, it writes `[]` and succeeds. It never instantiates or evaluates a registered strategy. Missing explicit-source rejection is fixed, but the documented autonomous producer remains a no-op. Regression: `test_absent_signal_source_must_not_claim_completed_screen`. Implement the screen or require validated upstream input; unavailable input must be distinguishable from a completed zero-candidate screen.

3. **P1: Bhavcopy session and value validation remain incomplete** (`scripts/ingest_daily_bhavcopy.py:100`, `:132`, `:145`). Four probes each demonstrate NORMAL publication for invalid evidence: a CSV without any date field; `CLOSE=nan`; close 999 outside low/high 99/105; and an empty series. The date column is optional, floats lack finiteness checks, OHLC bounds are incomplete, and series values are not validated. The original wrong-session/missing-columns probe now passes, but these cases remain fail-open. Regressions: `test_bhavcopy_rejects_unverifiable_or_invalid_rows` (four cases). Require verifiable target-session provenance, finite consistent OHLCV and valid series before publication. The current runbook happy-path fixture itself lacks dates, so it does not establish session verification.

4. **P1: Health reconciliation still returns GREEN for altered authoritative projections** (`scripts/verify_desk_health.py:155`, `:184`). After resealing the local manifest, independent probes change projected occupied slots to 3, cash to `nan`, or journal event sequence to 999999. Each returns successfully instead of rejecting. Occupied slots are never compared, NaN bypasses the tolerance comparison, and journal verification checks only count. This contradicts the submission's occupied-slot and event-sequence reconciliation claims. Regressions: `test_health_reconciles_authoritative_values` (three cases). Compare complete deterministic projections with SQLite, reject nonfinite values, and check key uniqueness, exact event identity/sequence and generation metadata. H7 remains optional when projections_dir is omitted (inspection; not counted as a dynamic failure).

## Confirmed improvements and scope

All 46 existing independent/runbook probes and the 153-test canonical suite pass together, supporting the fixture-isolation fix in this execution order. Explicit missing signal input now fails. Mandatory typed surveillance categories are validated. Null/zero digest and nonexistent-file cases fail as intended. Cash projection tampering to a finite value is detected. H3 uses DEFAULT_SECTOR_MAP and H6 scans all ledger payloads by inspection. These improvements do not resolve the reproduced blockers above.

Reproduction, with working directory explicitly `C:\Users\yashw\swing trades`:

```powershell
.\.venv\Scripts\python.exe shared/trust/artifacts/run_codex_day5_cd0b2bf_round5.py
```

Use a fresh dedicated basetemp name for reruns if needed. Exact child argv, absolute cwd, exit codes, unedited separate stdout/stderr, identity checks and SHA-256 seals are saved in `shared/trust/artifacts/CODEX-DAY5-PAPER-DESK-CD0B2BF-R5-*`. The new regression file is `shared/trust/artifacts/test_codex_day5_cd0b2bf_round5.py`.

Created only review report, runner, regression probes, dispatch helper and execution artifacts, including isolated test stores/projections. No implementation changes, merge, live trading, broker access or gate changes. Unrelated work was preserved. Nexus dispatch status is recorded separately; enqueueing is not peer acceptance or tri-agent consensus. Antigravity retains remediation ownership.
