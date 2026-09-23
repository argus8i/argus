# Track 2 Phase 1B2 — Official Source Ingestion Mandate

**Owner:** Antigravity  
**Reviewers:** Claude (red-team), Codex (systems/reliability)  
**Scope:** Track 2 only; paper observation only.

## Confirmed official inputs

- ASM: `https://www.nseindia.com/api/reportASM`
- GSM: `https://www.nseindia.com/api/reportGSM`
- Active F&O underlyings: `https://www.nseindia.com/api/underlying-information`

The F&O response contains `data.UnderlyingList`; each record has a `symbol`.

## Assigned files

Antigravity may create or modify only:

- `antigravity/daemons/track2_official_source_ingestor.py`
- `antigravity/daemons/exchange_circular_poller.py`
- `tests/test_track2_official_source_ingestion.py`

Do not modify the Phase 1A manifest, Phase 1B1 recorder, signal model, watchlists,
qualification counters, broker configuration, or any Track 1 file.

## Required design

1. Fetch all three official NSE endpoints as independent raw responses.
2. Persist raw response bytes before parsing, using atomic writes under the Track 2
   surveillance directory.
3. Record per source: exact URL, HTTP status, fetched-at timezone-aware timestamp,
   content type, byte length, SHA-256, raw relative path, and parser version.
4. Parse deterministically from persisted raw bytes, never from a caller-supplied
   derived symbol list.
5. Produce canonical uppercase, sorted, duplicate-free sets for ASM short-term,
   ASM long-term, GSM, and active F&O underlyings.
6. The poller must independently reopen every raw file, recheck every hash, replay
   the parser, and require exact equality with the derived lists before setting:
   `verified`, `source_authenticated`, and
   `lineage_bound_to_raw_parser_output` to true.
7. Fail closed on network failure, non-200 status, wrong official host/path,
   redirects outside the allowlist, HTML or unexpected content type, malformed JSON,
   missing schema nodes, invalid/duplicate symbols, empty F&O universe, timestamp
   ambiguity/future time, stale or wrong-session evidence, raw-path escape/symlink,
   hash mismatch, parser-version mismatch, or replay mismatch.
8. Never synthesize a clear surveillance state from missing data. Never mutate an
   existing immutable session snapshot.
9. No credentials, broker APIs, order routes, live orders, or qualification-count
   mutation.

## Tests required

Use local deterministic fixtures for positive and adversarial cases. Network tests
must be optional and cannot be required for the ordinary suite. Cover every failure
listed above plus atomic-write interruption and deterministic replay. Existing tests
must continue to pass.

## Acceptance evidence

- Focused test output.
- Full-suite output.
- `git diff --check` clean for owned files.
- Explicit statement of any unresolved P0/P1 issue.

Codex and Claude review independently after implementation. Antigravity does not
self-approve the change.
