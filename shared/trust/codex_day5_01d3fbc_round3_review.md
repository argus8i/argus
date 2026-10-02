# Independent Round 3 review

Verdict: **CHANGES_REQUIRED**
Review ID: `CODEX-DAY5-PAPER-DESK-01D3FBC-R3`
Reviewed HEAD: `01d3fbc8bf346700ace6d67908b44c6920abb63a`
Branch: `feature/day5-production-bridge-and-dossier`

Canonical promotion is not approved. This is a review-only assignment; Antigravity owns remediation. No production code was edited, merged or promoted.

## Independently executed evidence

- Original probes: 19 passed, exit 0 (8.64 seconds).
- Round 2 probes: 5 passed, exit 0 (1.86 seconds).
- Days 1-5 suite: 153 passed, exit 0 (19.56 seconds).
- Round 3 final probes: 4 failed, 1 passed, exit 1 (2.27 seconds).
- Submitted test log SHA-256 matches the submitted seal: `79a3d235f2c2caec1077ba27aec769293c67f06de6055f92a3fc9f901f6f8438`.
- Tracked review-source diff was empty. Existing unrelated untracked files and modified Nexus logs were preserved. Pytest emitted cache permission/path warnings; these did not account for assertion failures.

Exact argv, absolute cwd and exit codes are in the per-run JSON files under `shared/trust/artifacts/CODEX-DAY5-PAPER-DESK-01D3FBC-R3-*`. Raw subprocess stdout/stderr were saved as unedited bytes. All test databases are isolated under this workspace, away from the canonical paper ledger.

## Blocking findings

1. **P0: Same-day stop exit remains non-atomic.** `paper_desk_runner.py:1394,1454,1455` appends the SELL fill, then closure event, then position update in separate transactions. Injecting a crash before POSITION_CLOSED leaves sell cash credited on restart while the sold position is still OPEN. Failing regression: `test_same_day_exit_crash_cannot_credit_cash_with_open_inventory`. Commit sell event, position closure, reservation treatment and volume consumption atomically for this path too.

2. **P0: Method-identity branch defeats atomic execution.** `paper_store.py:469-474` detects an overridden/wrapped append_event and commits its event before BEGIN IMMEDIATE. A transparent forwarding wrapper plus an SQLite trigger that aborts position insertion produces a committed BUY cash debit with no position. Failing regression: `test_wrapped_append_event_does_not_escape_atomic_transaction`. Remove identity-dependent persistence behavior; inject crashes within the same actual transaction. Passing old crash probes do not establish rollback after an inserted event.

3. **P1: Regulatory ingestion still fabricates valid empty surveillance data from malformed input.** `ingest_daily_regulatory_data.py:111-121,202` converts `{}` into empty category lists and publishes NORMAL. Failing regression: `test_invalid_surveillance_schema_is_rejected`. Require a validated complete schema, official source provenance and session/effective-date validation before NORMAL. Hashing arbitrary bytes and using filesystem mtime alone do not establish source authority or daily freshness; undated F&O fallbacks remain.

4. **P1: Manifest validation verifies labels only.** `paper_desk_runner.py:1568` accepts exactly `{status: NORMAL, session_date: matching_date}` without an artifact path, hash, schema, coverage or relation to the supplied bars. The failing `test_matching_labels_alone_do_not_verify_manifest` records NORMAL equity from that dictionary and an empty bar map. Match-date rejection is fixed, cryptographic verification is not. Validate the canonical artifact and its relationship to bars before recording verified equity.

5. **P1: Autonomous runbook does not connect to its documented producer.** `YASHU_OPERATOR_RUNBOOK.md:98` calls `bhavcopy_downloader.py --date DATE`. The target accepts its first positional argument as the date (`bhavcopy_downloader.py:129`), downloads BSE data (`:58`), and writes a watchlist history CSV (`:37`). It does not create the NSE `data/bhavcopy/bhavcopy_DATE.csv` or `manifest_DATE.json` consumed by the next command. Candidate generation only reads an existing signals file; no producer is wired. The health command asserts six conditions, omits several checks in its seven-item table, accepts stale latest equity, and exits successfully when no equity exists. These static mismatches prevent the autonomous-operation claim. Formalize offline producer/consumer and health-failure tests before remediation; no exchange downloads were attempted during review.

## Remediations verified and limits

Caller-name eligibility bypass removal and fixed signal/evidence cutoff pass the previous adversarial regressions. Normal entry and next-session exit now use the transaction helper; split positions and replay marker share a transaction, and the generation manifest uses temporary-file replacement. These improvements do not cure the alternate exit and wrapped-method paths above. The missing-manifest retry diagnostic passed; no retry-blocking finding is asserted. Evidence remains BAR_SCENARIO_NON_QUALIFYING; no live-capital gate or qualification criteria were changed.

## Changed files

Created this report, `shared/trust/artifacts/test_codex_day5_01d3fbc_round3.py`, `shared/trust/artifacts/run_codex_day5_01d3fbc_round3.py`, and review execution/inspection/hash/dispatch artifacts under the same prefix. Nexus dispatch may append its existing dialogue/event logs. Production source and existing peer probes remain unchanged.
