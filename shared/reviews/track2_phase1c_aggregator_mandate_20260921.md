# Track 2 Phase 1C — Read-Only Verdict Aggregator Mandate

## Objective

Build a fail-closed accountant for sealed Track 2 paper-session evidence. It
must derive the 60-session and 20-fill counters from verified artifacts and make
every pending, void, corrupt, orphaned, or excluded day visible.

## Required behavior

1. Read, but never modify, `shared/track2_liquid/sessions/`.
2. Verify the global attempt ledger before counting anything.
3. Treat `PENDING`, `VOID`, and `FINALIZED` as distinct operational outcomes.
4. For a `FINALIZED` day, verify the hashed verdict and reproduce it from the
   locked preregistration, preflight, manifest, streams, checkpoints, reference
   files, and any separately hashed fill-evidence artifact.
5. Require the terminal attempt record to bind the exact verdict hash.
6. Use `compute_gate_counts()` only after all individual verdicts pass.
7. A global integrity failure produces zero trusted counters, never partial
   optimistic totals.
8. Detect evidence directories that have no attempt-ledger entry.
9. Return one small human-readable status model and optionally write a derived
   report outside the evidence directory using atomic replacement.

## Prohibited behavior

- No broker APIs, credentials, order routes, live orders, P&L mutation, trade-log
  edits, or Track 1 access.
- No counting historical, replayed, smoke-test, pilot, or manually reconstructed
  sessions.
- No trusting copied counter fields without evidence reproduction.

## Acceptance

- Adversarial tests for missing, corrupt, duplicate, pending, void, orphaned,
  tampered, and valid sessions.
- Full first-party suite green.
- Independent Claude and Antigravity review with no unresolved P0/P1 defect.
