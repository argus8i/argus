# Track 2 Phase 1 transition contract

Owner: Codex. Antigravity remains integration owner. Claude reviewed the plan.

This phase evaluates research exit intents, not order executions. Quote/peak/low
touches cannot create fills, qualify trades, or book realized P&L.

- Active + stop touch -> PENDING_STOP_EXIT.
- Active + target touch -> PENDING_TARGET_EXIT (tranche 1 only).
- Active + EOD -> PENDING_EOD_SQUAREOFF (MIS tranche 1 only).
- Stop and target in the same observation -> stop pending, AMBIGUOUS_ORDER.
- Pending/cancel/unfilled states -> preserve state when the caller supplies
  the prior state; there is no durable persistence yet and positions remain disabled.
- PARTIAL requires remaining-quantity and fill evidence, so Phase 1 rejects it.
- Legacy terminal state -> reject as unverified until a fill ledger exists.

No trailing amendment based on unordered OHLC extremes in Phase 1. Stop and
target remain structural research levels, not exchange-ready tick-rounded orders.
Missing order type defaults SL_LIMIT, explicit SL_M accepted only as intent;
aliases and unknown types are rejected. Legacy UNFILLED_TRIGGERED stays pending.
Corrupt/mismatched prior states raise errors, never reset active.

Historical claims are tagged, not deleted. Radar hardcoded positions are disabled;
qualification is frozen. Durable order/fill persistence, point-in-time feeds and
instrument-specific tick metadata are Phase 2 acceptance blockers, not claimed done.
