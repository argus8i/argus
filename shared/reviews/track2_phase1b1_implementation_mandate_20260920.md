# Track 2 Phase 1B1 — Antigravity Implementation Mandate

Implement the first integration slice. You own only:

- `antigravity/daemons/track2_session_recorder.py` (new)
- `tests/test_track2_phase1b_integration.py` (new)

Do not modify the existing radar, browser bridge, `session_manifest.py`, logs, or
production artifacts in this slice.

## Required behavior

Build a paper-only `Track2SessionRecorder`/coordinator API that:

1. uses Phase 1A `write_locked_preregistration` and `evaluate_session` rather than
   duplicating their validation;
2. accepts only the local Track 2 browser snapshot schema after
   `feed_validity.check_feed` passes;
3. appends canonical JSONL events under a caller-supplied session directory using
   one writer, monotonic source timestamps, explicit flush/fsync, and snapshot
   deduplication;
4. labels ordinary watchlist LTP observations no higher than `E1_BAR_POSSIBLE`;
5. may label selected-stock displayed depth `E2_MARKETABLE_DEPTH` only when the
   active symbol, both sides, finite prices, and nonnegative quantities validate;
6. refuses any E3 claim and never creates/synthesizes trade IDs;
7. supports canonical SIGNAL event append through the same writer, bound to the
   frozen `order_rules` hash and valid 09:30–15:15 time window;
8. builds reference records and a data manifest from actual files after close;
9. evaluates and atomically persists a verdict only after 15:30; repeated close is
   idempotent and cannot increment counters twice;
10. records real gaps and out-of-order input rather than smoothing/backfilling;
11. contains no broker order route, POST/PUT/DELETE network path, credential read,
    or import capable of order placement.

Keep the API dependency-injected and testable without a live browser or clock.
Tests must cover valid zero-signal E1 session, E2-but-zero-fill behavior, duplicate
snapshot rejection, >5-second gap VOID, hidden/stale feed rejection, E3 rejection,
signal binding, early close rejection, crash/incomplete manifest, idempotent close,
and a static no-order-path guard. Use temporary directories only.

Run focused and full tests. Report changed files, exact results, unresolved defects,
and `IMPLEMENTED` or `BLOCKED`. Preserve Rule 1 and Rule 11.
