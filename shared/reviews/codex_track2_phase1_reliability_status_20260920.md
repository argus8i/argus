# Track 2 Phase 1 Reliability Status — Codex

**Date:** 20 September 2026  
**Scope:** Track 2 only — liquid high-beta 15-minute ORB research system  
**Role:** Senior Systems, Execution-Reality & Reliability Engineer  
**Capital state:** 100% cash; observation only  
**Verified qualification gate:** 0/60 prospective sessions; 0/20 evidenced fills

## Verdict

**CONDITIONALLY APPROVED for fail-closed Phase 1 research operation.**  
**BLOCKED for qualification claims, paper-trade P&L, and any live execution.**

The system now fails closed when market inputs, surveillance provenance, execution
evidence, or point-in-time lineage are missing. It must not infer fills from candle
touches, report realized P&L without fill evidence, or carry legacy simulated
positions into the authoritative runtime state.

## Repairs verified

1. Signal evaluation is separated from execution evidence. Target, stop, and EOD
   touches create pending instructions only; realized P&L remains zero.
2. The screener emits `RESEARCH_ORB_HYPOTHESIS`, never a trade authorization.
   Invalid, non-finite, boolean, missing-regime, zero-size, and unsupported order
   inputs fail closed.
3. The universe scanner has no permissive fallback. Candidates require explicit
   Track 2 eligibility inputs and remain research-only.
4. The live radar imports and invokes surveillance correctly, consumes only a
   same-session research universe, publishes 0/60 and 0/20, disables hardcoded
   positions, and cannot append qualification rows while the hold is active.
5. Surveillance snapshots enforce path confinement, raw-file hashing, schema and
   parser metadata, canonical symbols, effective-session matching, and timestamp
   ordering. Because the current feed does not cryptographically bind parsed lists
   to a trusted official downloader/parser replay, it honestly reports integrity
   checks but not source authentication, and disqualifies every symbol.
6. Historical Track 2 rows, P&L, positions, and counters are retained only as
   unverified reconciliation material. The active CSV is header-only and the old
   CSV is recoverably quarantined.
7. Regression and adversarial coverage includes malformed values, ambiguous bars,
   order-type rejection, allocation rounding, stale cross-session artifacts,
   surveillance provenance, radar integration, and qualification-log suppression.

## Verification result

- Project-owned automated suite (`tests/`): **308 passed**.
- Unscoped repository-wide discovery also enters vendored Chronos/FinGPT test trees
  and stops during collection because their optional `pandas`, `datasets`, `torch`,
  `matplotlib`, and `structlog` dependencies are not installed. This does not alter
  the 308 project-test result, but test discovery should be scoped explicitly.
- Track 2 authoritative state: **0 qualifying sessions, 0 evidenced fills**.
- No source-authenticated surveillance decision is presently available.
- No real order path is enabled or approved.

## Required Phase 2 work

1. Build source-specific official exchange downloaders and parsers for F&O
   membership and ASM/GSM data. Persist raw response bytes and replay the pinned
   parser so the derived symbol sets are reproducibly bound to those bytes.
2. Build a point-in-time market-data contract: immutable opening-range bars,
   same-bucket historical volume known before decision time, quote timestamps,
   benchmark/breadth timestamps, and explicit decision timestamps. Eliminate
   completed-bar hindsight and synthetic entry-price assumptions.
3. Build a durable atomic order/fill ledger. Record broker acknowledgement,
   exchange order ID, partial fills, remaining quantity, fill time/price, rejection,
   cancellation, restart recovery, tick size, and instrument metadata. Only this
   ledger may create realized P&L.
4. Add actual charges and measured slippage, then collect the required prospective
   60 sessions and 20 evidenced paper fills before estimating expectancy.

## Peer-review state

Claude's pre-review and post-review are recorded in
`shared/reviews/claude_phase1_plan_20260920.md` and
`shared/reviews/claude_phase1_postreview_20260920.md`. Antigravity remains the
integration owner and must independently accept or challenge this checkpoint before
Phase 2 changes are treated as core-model consensus. A final read-only Antigravity
review was dispatched through the tri-agent bus after the 308-test run, but timed out
after 180 seconds without returning a verdict; therefore Antigravity acceptance is
not claimed.
