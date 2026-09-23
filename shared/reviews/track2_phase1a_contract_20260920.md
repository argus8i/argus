# Track 2 Phase 1A — Evidence Contract

**Date:** 20 September 2026  
**Status:** Implemented; pending final independent re-review  
**Deciders:** Yashu, Antigravity, Claude, Codex  
**Capital state:** Paper only; zero live orders

## Decision

Phase 1A builds evidence infrastructure, not a new alpha model. The baseline
15-minute ORB strategy remains frozen. Gate counters are computed from immutable
session artifacts rather than entered manually.

## Evidence classes

| Class | Meaning | Counts toward 20 |
|---|---|---:|
| `E0_SIGNAL_ONLY` | A prospective signal exists | No |
| `E1_BAR_POSSIBLE` | OHLCV indicates a possible touch/trade-through | No |
| `E2_MARKETABLE_DEPTH` | A timestamped displayed-depth estimate exists | No during Phase 1A |
| `E3_TICK_QUEUE` | Continuous tick/top-of-book evidence proves queue-plus-size turnover | Yes, only when state is `FILLED` |

Aggregate 1/5/15-minute volume multiples are diagnostics only. They never prove
FIFO position or a paper fill.

## Counter rules

- A session counts toward 60 only after immutable preregistration before 09:15
  IST, a parent-level hash-chained preregistration anchor, authenticated fail-closed
  preflight, a complete data manifest within its preregistered maximum five-second
  capture gap, and explicit session closure at or after 15:30 IST.
- A valid zero-signal session counts toward 60 and adds zero fills.
- Only fully validated `E3_TICK_QUEUE` entries count toward 20.
- Manifest hashes, row counts, timestamps, gaps, quote values, queue rank,
  at-or-better traded volume, and the queue-clear timestamp are recomputed from
  the actual JSONL evidence files. Caller-provided summaries are never trusted.
- Signals and order specifications are derived from hashed market-stream events;
  callers cannot supply the session's signal count. E3 volume begins only after
  the manifested signal and order arrival, with the frozen latency delay enforced.
  A conservative turnover haircut is applied before queue-plus-size clearance is
  evaluated.
- Continuity is checked for the traded symbol, not merely across a mixed-symbol
  file. Trade prints require unique source trade identifiers, and claimed costs
  must meet the frozen cost model's minimum deduction.
- Runtime wall-clock values are read internally. The public API cannot inject a
  registration or evaluation time.
- Missing, stale, unauthenticated, malformed, or tampered evidence makes the
  session `VOID`.
- The four prior sessions are `PILOT_UNVERIFIED` and count toward neither gate.

## Ownership

- Codex owns `antigravity/models/session_manifest.py`, this contract, and
  `tests/test_session_evidence_20260920.py` for Phase 1A.
- Antigravity owns later runner/radar integration after this slice passes review.
- Claude performs read-only adversarial review.
- Core strategy files are unchanged in Phase 1A.

## Trust boundary

The digest and parent-level chain detect accidental corruption and ordinary
post-hoc edits. They are not a security boundary against an operator with full
filesystem access, who can rewrite both evidence and its local chain. Independent
broker/exchange evidence remains preferable when later integration makes it
available.

## Deferred

- Tick/WebSocket capture and its continuity probe.
- Session-runner integration.
- Cost/slippage engine and fill engine.
- Model tuning, ML, regime optimization, and live broker execution.
