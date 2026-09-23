# Track 2 Phase 1D Implementation Record

**Date:** 21 September 2026  
**Scope:** Track 2 only  
**Mode:** Paper-only, non-counting rehearsal

## Implemented contract

- Added an immutable `qualification_mode` to preregistration, preflight, and the
  first `PENDING` attempt record.
- Added terminal `REHEARSAL` session and attempt states. A rehearsal can never
  transition to `FINALIZED` or contribute to either Rule 1 counter.
- Resume requires the runtime mode to match the mode frozen in both the ledger
  and preregistration.
- The independent aggregator requires an expected mode and rejects cross-mode
  evidence roots.
- Added a broker-incapable rehearsal runner using dedicated `rehearsals/` and
  `rehearsal_surveillance/` roots.
- Live browser input must be valid, visible, contain at least four instruments,
  and be no more than five seconds old.
- Added a plain-English operations runbook and example configuration.

## Verification

- Focused Phase 1D plus aggregator suite: **23 passed**.
- Project-owned suite: **475 passed** in the clean final run. Earlier state-test
  failures were traced to active daemon output and obsolete assumptions that
  research-only candidates/legacy zero-signal rows must be absent. The repaired
  assertions still require zero positions, zero shares/notional, and no fills.
- Unscoped repository discovery also enters vendored Chronos/FinGPT suites and
  fails collection because optional third-party ML packages are not installed;
  those suites are not part of Track 2's first-party verification boundary.

## Current verdict

**IMPLEMENTED, PEER-REVIEWED, LIVE RUN PENDING.** No live rehearsal has been
claimed. Claude found no P0/P1 but requested P2 resume/universe/error handling;
those changes were implemented and regression-tested. Antigravity approved the
implementation. Both Claude and Antigravity then approved the hardening delta
with no P0/P1 findings. Final release still requires one actual market-day
rehearsal using authentic sources.
