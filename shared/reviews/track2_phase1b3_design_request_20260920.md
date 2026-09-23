# Track 2 Phase 1B3 — Coordinator Design Review

**Status:** DESIGN ONLY — no implementation authorized by this document  
**Track:** Track 2 only; paper observation only

## Objective

Connect the approved Phase 1B2 official-source snapshot to the approved Phase 1B1
session recorder through a small fail-closed coordinator.

## Proposed lifecycle

1. Before the session cutoff, ingest and replay-verify official ASM, GSM, and F&O
   sources using Phase 1B2.
2. Freeze a session universe that is an explicit subset of the verified current F&O
   list and excludes every ASM/GSM symbol.
3. Hash and lock configuration, universe, order rules, cost model, evidence limits,
   and source references before market open.
4. Create the Phase 1B1 recorder only after every preflight field is supported by a
   real immutable source artifact.
5. During market hours, read only prospective browser snapshots and pass them to the
   recorder. Browser observations may produce E1/E2 only, never E3 fills.
6. Finalize exactly once after session close. Do not update qualification counters or
   trade logs directly; aggregation reads immutable verdicts separately.

## Non-negotiable exclusions

- Do not call or import `track2_live_radar.py`.
- Do not read, copy, store, or validate `enctoken`, cookies, API keys, or credentials.
- No Kite OMS historical endpoint, broker order route, Yahoo fallback, POST/PUT/DELETE,
  direct P&L, trade-log append, or qualification-counter mutation.
- Do not use `dynamic_universe.json` while it remains quarantined.
- Do not treat browser depth as a completed trade.

## Design questions

1. Phase 1B1 requires `band_check` and `band_source_sha256`. For a current verified
   F&O underlying subject to NSE dynamic operating ranges, what is the smallest honest
   evidence artifact? Is current official F&O membership plus a versioned static policy
   reference sufficient, or must another daily official source be ingested?
2. Should an empty eligible universe produce a valid zero-signal counted session, or a
   VOID session? State the anti-selection/data-quality reasoning.
3. What exact coordinator states and crash-recovery transitions are required?
4. Which timestamps must be caller-injected and which must come from sealed artifacts?
5. What adversarial tests are mandatory before integration?

Return P0/P1 design objections and a concrete minimal contract. Do not edit code.
