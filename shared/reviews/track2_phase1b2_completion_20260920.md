# Track 2 Phase 1B2 — Completion Record

**Date:** 20 September 2026  
**Scope:** Official NSE surveillance and F&O source ingestion  
**Final verdict:** APPROVED for paper-observation infrastructure

## Outcome

Phase 1B2 now fetches and independently persists the three official NSE inputs:

- ASM: `https://www.nseindia.com/api/reportASM`
- GSM: `https://www.nseindia.com/api/reportGSM`
- F&O underlyings: `https://www.nseindia.com/api/underlying-information`

Every source is bound to its exact endpoint, HTTP status, JSON content type,
timezone-aware fetch time, byte length, SHA-256, session-specific raw filename,
and parser version. The poller reopens the raw bytes, verifies containment and
hashes, replays the parser, and requires exact equality with the derived symbol
sets before reporting authenticated lineage.

## Material defects found and repaired

The initial Antigravity implementation timed out and was not accepted. Independent
inspection found that its fixtures used invented ASM/GSM schemas. The production
parser was corrected to the live NSE structures: `shortterm.data`,
`longterm.data`, a top-level GSM array, and `data.UnderlyingList` for F&O.

Subsequent red-team review identified and closed:

1. cross-session raw-file reuse;
2. unbounded evidence age;
3. empty ASM/GSM lists being interpreted as clear;
4. retry lockout after partial ingestion;
5. concurrent snapshot overwrite;
6. stale per-source timestamps hidden by a fresh top-level timestamp;
7. missing top-level/per-source and composite-hash binding.

## Verification

- Focused surveillance/source suite: **69 passed**.
- First-party project suite (`tests/`): **408 passed**.
- Temporary non-counting live-source smoke: **74 ST-ASM, 145 LT-ASM, 77 GSM,
  210 F&O underlyings**.
- Assigned-file `git diff --check`: clean, except the existing CRLF conversion
  warning for `exchange_circular_poller.py`.
- Claude final closure verdict: **APPROVED**, no remaining P0/P1.
- Antigravity signed bus response: **APPROVED**, cryptographic envelope
  verification succeeded, no remaining P0/P1.

## Gate status

This approval is for paper-observation infrastructure only. The temporary live
smoke is not a prospective paper session and creates no fill evidence.

- Qualified prospective sessions: **0/60**
- Realistically fillable paper trades: **0/20**
- Live capital: **prohibited by Rule 1**

## Next phase

Phase 1B3 should connect the approved source snapshot and the approved Phase 1B1
recorder through a small coordinator. It must preregister before 09:00 IST, refuse
to start without authenticated surveillance/F&O evidence, ingest only prospective
browser observations, finalize one immutable session verdict, and never create E3
fill evidence from browser depth alone.
