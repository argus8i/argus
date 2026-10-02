# Independent Round 7 review

**Verdict: CHANGES_REQUIRED**  
**Review ID: CODEX-DAY5-PAPER-DESK-369D464-R7**  
Reviewed commit: `369d46406810ee3393fefce824d3791fc13a4fbb`, branch `feature/day5-production-bridge-and-dossier`; requested base `8d144ff`.

The nine Round 6 examples are fixed, but both blocking acceptance requirements remain incomplete. APPROVED cannot be issued.

## Independent checks

| Check | Result | Child exit code |
|---|---|---|
| Canonical suite and all prior independent/runbook probes | 217 passed in 33.54s | 0 |
| New Round 7 regression probes | 9 failed in 4.50s | 1 |
| Start/end HEAD | Both equal submitted commit | 0 |
| Start/end reviewed source diff against HEAD | Empty | 0 |

Exact child argv, cwd, exit code, and raw unedited stdout/stderr are preserved in artifacts with prefix `CODEX-DAY5-PAPER-DESK-369D464-R7`. The wrapper returns 0; pytest child results above determine acceptance. Submitted log hash/size are recorded in submitted-hash.json.

## Blocking findings

1. **P1: Source verification still fails open and consumed evidence is incompletely bound** (`antigravity/paper/paper_desk_runner.py:1645`, `:1678`). Seven regression cases seal SQLite equity despite invalid evidence: missing source/digest with active portfolio activity; missing source/digest on retry; source lacking the consumed symbol; source lacking a date column; NaN source close; different source open; wrong source series (BE rather than EQ). The explicit activity/retry exemption defeats fail-closed provenance precisely when economics exist. Source matching checks only close when the symbol happens to exist, tolerates absent date, ignores series/OHLV, and NaN defeats the difference comparison. Require verified provenance unconditionally, including retries and active portfolios. Reuse the full ingestion validation contract, reject missing coverage, reconcile consumed symbol/series/session/OHLCV and reject nonfinite source values. Keep uncommitted projection availability separate from SQLite sealing. Regression: `test_source_gate_applies_to_all_consumed_evidence` (seven cases).

2. **P1: Complete authoritative projection reconciliation remains incomplete** (`scripts/verify_desk_health.py:242`, `:292`). Two regression cases return healthy after resealing the local manifest: open_positions.csv symbol changed to FORGED; symbol column removed entirely from canonical_paper_journal.csv. Position reconciliation handles numeric fields only; journal reconciliation skips payload keys absent from CSV. Require the complete projection schema and compare every authoritative projected value, including strings and nulls; validate unique key sets and metadata rather than only row counts. Regression: `test_complete_projection_contract` (two cases).

## Reproduction

Every command uses working directory `C:\Users\yashw\swing trades`.

```powershell
.\.venv\Scripts\python.exe shared/trust/artifacts/run_codex_day5_369d464_round7.py
```

Use fresh basetemp names on reruns. Tests and runner live under shared/trust/artifacts; raw outputs and command metadata have the review-ID prefix. SHA-256 seals are in the hashes JSON.

Created only this report, the Round 7 tests, execution runner, dispatch runner, and execution artifacts including isolated pytest stores/projections. No implementation changes, merge, broker access, live orders, or gate changes. Unrelated work preserved. Initial inventory emitted permission warnings for old unrelated artifact directories; rg was unavailable, so PowerShell inspection was used. These inventory limitations did not affect test execution. Antigravity retains remediation/integration ownership. Nexus enqueue status is recorded separately; it is not peer acceptance or consensus.
