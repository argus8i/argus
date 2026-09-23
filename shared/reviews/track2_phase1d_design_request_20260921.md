# Track 2 Phase 1D — Non-Counting Rehearsal Design Review

Review only; do not edit code. Track 2 only. Rule 1 remains absolute.

## Objective

Design one live-market rehearsal of the complete pre-open, intraday recording,
restart, finalization, and aggregation workflow. The rehearsal must be fully
auditable but structurally incapable of incrementing either the 60-session or
20-fill qualification gate.

## Design question

The current status model has `COUNTED`, `VOID`, and `PILOT_UNVERIFIED`; the
attempt ledger has `PENDING`, `VOID`, and `FINALIZED`. A valid rehearsal should
not be mislabeled as corrupt (`VOID`), historical (`PILOT_UNVERIFIED`), or
qualifying (`COUNTED`). Recommend the smallest fail-closed state/data-contract
change that makes rehearsal provenance explicit.

Address:

1. Whether to add `SessionStatus.REHEARSAL` and terminal attempt outcome
   `REHEARSAL`, or use a separate isolated evidence root without changing enums.
2. Where an immutable `qualification_mode` must be frozen and validated.
3. How recorder recovery, coordinator state, and Phase 1C aggregation should
   treat the rehearsal.
4. How to prevent changing a rehearsal into a qualifying session after market
   data is known.
5. Minimum adversarial tests and whether a rehearsal requires the same evidence
   checks as a qualifying prospective session.

Return a recommended contract, P0/P1 objections, and one design verdict:
`APPROVED`, `CONDITIONALLY_APPROVED`, or `BLOCKED (P0/P1: reason)`.
