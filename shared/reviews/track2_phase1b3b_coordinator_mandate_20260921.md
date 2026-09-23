# Track 2 Phase 1B3B — Paper Session Coordinator Mandate

**Date:** 21 September 2026  
**Track:** Track 2 only  
**Mode:** Prospective paper observation only

## Objective

Connect the approved Phase 1B2 official-source ingestor to the hardened Phase
1B3A recorder through one fail-closed daily lifecycle.

## Lifecycle

1. Append the session's unique `PENDING` attempt before any source ingestion.
2. Ingest and replay-verify the official NSE ASM, GSM, and F&O snapshot before
   09:00 IST.
3. Intersect the configured candidates with the current F&O list and remove all
   ASM/GSM names. Require at least four eligible symbols.
4. Materialize four distinct, contained, immutable role artifacts: `FNO`,
   `SURVEILLANCE`, `BAND_POLICY`, and `UNIVERSE`.
5. Lock preregistration and seal authenticated preflight before market open.
6. Construct the hardened recorder only after all earlier gates pass.
7. Accept prospective snapshots/signals through the recorder. Browser data can
   create E1/E2 evidence only.
8. Finalize exactly once after close. Immutable verdict aggregation remains a
   separate phase.

## Fail-closed requirements

- Any preparation failure appends terminal `VOID` where the attempt registry is
  still writable.
- Existing artifacts are never overwritten.
- Resumption requires exact locked evidence; no second daily attempt is created.
- Empty or fewer-than-four universes are `VOID`, never counted zero-signal days.
- The band-policy artifact must identify both NSE FAOP/62241 and its later
  modification FAOP/63405; membership alone cannot invent band policy.
- Source replay, hashes, session dates, symbol sets, and role paths must agree.

## Prohibited capabilities

- Broker APIs, credentials, cookies, enctoken, holdings, or orders.
- `track2_live_radar.py`, Kite OMS history, Yahoo fallback, E3 fabrication,
  direct P&L, trade-log writes, or counter mutation.
- Any Track 1 import or rule.

## Assigned implementation

- `antigravity/daemons/track2_session_coordinator.py`
- `tests/test_track2_phase1b3b_coordinator.py`

Existing Phase 1B2/1B3A modules may be called but not weakened.
