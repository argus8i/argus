# Track 2 Phase 1B3A Red-Team Status — 20 September 2026

## Verdict

**CONDITIONALLY APPROVED for research-only prospective paper qualification.**

Phase 1B3A now rejects post-close creation of `preflight.json`, requires four
distinct source roles, binds a frozen universe, records a hash-chained attempt
ledger, and automatically emits stream checkpoints. The migrated focused suite
passes, but independent Claude review identified two remaining P0 evidence
integrity defects. Those defects were repaired and independently re-reviewed.

## P0 resolution

1. Public `clock=` injection is rejected. Production timestamps come from the
   recorder-owned clock; the private `_test_clock` keyword is retained solely as
   a test seam.
2. Every checkpoint now includes a byte count and SHA-256 of the exact stream
   prefix. Evaluation requires the first checkpoint by 09:21 IST, active-growth
   cadence no greater than 360 seconds, a final checkpoint at/after 15:30, and an
   exact final digest/row/byte binding.

## Residual boundary and P1 follow-ups

The local chain is **tamper-evident, not an external trusted timestamp**. A local
administrator who controls the system clock and can rewrite every project file
could reconstruct a consistent history. That administrator-level threat is
outside the present research qualification boundary and must not be described as
cryptographic proof of real-world time.

- Require `PENDING` before open and before the first event; bind terminal
  `FINALIZED`/`VOID` to the persisted verdict.
- Make every failure path persist a terminal VOID attempt instead of raising and
  disappearing.
- Validate event `recorded_at`, signal freshness, `event.symbol == order_spec.symbol`,
  and every universe-bearing field.
- Validate the universe schema and reject symlinked evidence artifacts.
- Parse and validate source-role contents; hashes alone do not establish that a
  file is actually an F&O, surveillance, or band-policy artifact.
- Bind chain heads outside the individual session directory or explicitly state
  that local administrator-level filesystem rewriting is outside the threat model.

## Verification performed

- `tests/test_track2_phase1b3a_hardening.py`: **14/14 passed**.
- `tests/test_session_evidence_20260920.py`: **43/43 passed**.
- `tests/test_track2_phase1b_integration.py`: **20/20 passed**.
- Claude final read-only review verdict: **CONDITIONALLY_APPROVED; no remaining P0**.
- Antigravity dispatch timed out, but its partial prefix-binding patch was
  inspected, completed, and independently tested by Codex before review.

## Gate status

- Rule 1 remains active: **0/60 sessions, 0/20 fillable paper entries**.
- No historical pilot is promoted by these changes.
- Phase 1B3 coordinator work may now begin under the stated trust boundary.

## 21 September Debug Pass

Codex performed a fresh compile, failure-path inspection, focused regression run,
and full-project test run. Three defects were corrected:

1. `record_signal()` now rejects missing, naive, wrong-day, or stale recorder
   clocks instead of raising or recording a retrospectively timed signal.
2. A corrupt preflight digest now closes as a persisted `VOID` verdict and
   terminal `VOID` attempt rather than aborting before evidence finalization.
3. A `PENDING` attempt recorded at or after 09:15 IST can no longer qualify a
   session.

New adversarial coverage verifies invalid/stale signal clocks and tampered
preflight closure. Final verification: **424/424 tests passed**.
