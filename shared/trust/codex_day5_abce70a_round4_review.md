# Independent Round 4 review

**Verdict: CHANGES_REQUIRED**  
**Review ID: CODEX-DAY5-PAPER-DESK-ABCE70A-R4**  
Reviewed HEAD: `abce70afcbaa0fa4fd026c971082b8f966dbb262`, branch `feature/day5-production-bridge-and-dossier`, requested base `8d144ff`.

## Verification

| Check | Result | Exit code |
|---|---:|---:|
| Original probes | 19 passed | 0 |
| Round 2 probes | 5 passed | 0 |
| Round 3 probes | 5 passed | 0 |
| Days 1?5 suite | 153 passed | 0 |
| Runbook wiring | 8 passed | 0 |
| New Round 4 adversarial probes | 9 failed | 1 |

Submitted log SHA-256 independently matches `023D16262096790CF81761B59775B009B32CCC6CF135232DC7A0BD6974B48582`. Pytest cache warnings are preserved in raw logs. Reviewed implementation files match HEAD; unrelated working-tree changes were preserved. This is an independent review, not tri-agent consensus or canonical promotion approval.

## Confirmed remediations

Same-day sell closure now commits position, consumed volume, sell fill and close events together. Both crash atomicity and forwarding-wrapper rollback regression probes pass. The method identity branch was removed. No blocking defect was reproduced in these two remediations by the executed probes.

## Blocking findings

1. **P1 ? Manifest verification remains fail-open** (`antigravity/paper/paper_desk_runner.py:1572?1580`). Presence of a provenance key is treated as verification: `source_sha256=None`, an arbitrary all-zero digest, and a nonexistent `source_file` each seal NORMAL daily equity with no bars. An unsealed pending session also accepts bare labels on retry with no new data. Nonempty bars likewise bypass provenance verification by inspection. Compute and compare the hashes of the actual consumed data, validate session and coverage, and require the same verified evidence on retry. The old Round 3 test named ?retried with verified data? supplies only labels and must be corrected; its passing result does not establish verification. Four new failing cases formalize these bypasses.

2. **P1 ? Surveillance schema validation is incomplete** (`scripts/ingest_daily_regulatory_data.py:115?126`). `any` required key allows `{"gsm":[]}` and silently invents missing ASM lists. A string `gsm="INFY"` becomes character symbols rather than rejecting malformed data. Both cases reproduce. Require complete typed arrays of valid symbols (or explicitly verified complete source coverage), rejecting missing categories and scalar values before sealing a snapshot.

3. **P1 ? Candidate producer does not perform the documented screen** (`scripts/generate_candidate_signals.py:49?65`; runbook Phase 1 Step 2). The documented default command runs no registered strategy or universe screen; it writes an empty list and reports success. An explicitly supplied nonexistent source also silently produces an empty set. A failing probe formalizes missing-source rejection. Implement the documented producer or require and validate an upstream produced signal artifact, failing closed on unavailable input. Validate entry session and actual cutoff provenance; the unused fixed `decision_ts` does not demonstrate pre-open generation.

4. **P1 ? Bhavcopy ingestion seals invalid/wrong-session data** (`scripts/ingest_daily_bhavcopy.py:75?140`). A CSV containing only `SYMBOL,DATE` with a prior-session row is accepted as NORMAL for the requested session, despite missing all prices, volume and series. SHA-256 proves byte integrity, not official origin, correct session or valid market data. The new probe reproduces this acceptance. Validate supported official schemas, dated source provenance, series, numeric OHLCV, consistency and target-session rows before publishing. Keep malformed or wrong-session input pending rather than sealed.

5. **P1 ? Health reconciliation does not compare values to SQLite** (`scripts/verify_desk_health.py:129?170`). Changing projected `cash_ledger_rs` to `999999999` and updating the local generation manifest digest returns GREEN: row counts and self-reported file hashes are not value reconciliation. The new failing probe reproduces this. Compare deterministic canonical projections/values with authoritative SQLite state and validate complete generation metadata. Additional inspection concerns: H3 substitutes sleeve for sector although positions do not carry a sector field; H6 checks only the last event and skips absent evidence status; H7 can be omitted through the optional argument. These are not independently claimed as dynamic probe failures in this round, but must be reconciled before describing all seven checks as fail-closed.

## Reproduction artifacts and scope

Run ` .\.venv\Scripts\python.exe shared/trust/artifacts/run_codex_day5_abce70a_round4.py` from `C:\Users\yashw\swing trades` to reproduce the prior-probe, suite and runbook runs. New failing tests: `shared/trust/artifacts/test_codex_day5_abce70a_round4.py`; execute with `.\.venv\Scripts\python.exe -m pytest shared/trust/artifacts/test_codex_day5_abce70a_round4.py -v --basetemp shared/trust/artifacts/CODEX-DAY5-PAPER-DESK-ABCE70A-R4-adversarial-reproduction-tmp` (use a fresh dedicated directory).

Artifacts under `shared/trust/artifacts/CODEX-DAY5-PAPER-DESK-ABCE70A-R4-*` contain exact argv, absolute cwd, exit codes, separate unedited stdout/stderr, identity checks and hash seals. New review files only: this report, Round 4 runner, regression probe file, dispatch helper and execution evidence including isolated test databases/CSVs. No implementation fixes, live broker activity, gate changes or merge performed. Antigravity retains remediation ownership; review remains CHANGES_REQUIRED.
