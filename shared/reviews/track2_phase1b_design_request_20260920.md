# Track 2 Phase 1B — Tri-Agent Design Request

Phase 1A is approved. Design the smallest safe integration slice that connects it
to the existing Track 2 paper workflow. Inspect:

- `antigravity/models/session_manifest.py`
- `antigravity/daemons/track2_live_radar.py`
- `antigravity/daemons/track2_kite_bridge.py`
- `antigravity/daemons/exchange_circular_poller.py`
- `antigravity/models/track2_universe_scanner.py`
- relevant Track 2 tests and launchers

Known constraint: the current browser bridge overwrites a point-in-time JSON
snapshot and does not expose exchange trade identifiers. It must not be relabeled
as E3 tick/queue evidence. Yahoo fallback is research-only and cannot satisfy an
authenticated qualification contract.

Return a concrete Phase 1B design covering:

1. exact files/modules to add or change;
2. how preregistration, preflight, stream capture, signal events, manifest closure,
   and verdict persistence connect end-to-end;
3. which evidence class the current data can honestly support;
4. how the design remains physically incapable of live order placement;
5. testable acceptance criteria and failure recovery;
6. any P0/P1 objection to starting implementation;
7. recommended file ownership for Antigravity, Claude, and Codex.

Do not modify files in this design pass. Finish with `START`, `REVISE`, or `BLOCK`.
