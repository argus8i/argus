# Claude Pre-Implementation Review: Track 2 Phase 1 Plan (2026-09-20)

Scope: Track 2 only (Rule 11). Source read, not modified. Reviewed:
`antigravity/models/two_tranche_exit_model.py`, `antigravity/models/liquid_momentum_screener.py`
(`calculate_position_size`), `antigravity/daemons/track2_live_radar.py` (portfolio loop, ~L540-632),
and the `qualified` logic in `track2_universe_scanner.py`. I did not run tests.

## Verdict
The plan is directionally right. Approve with the amendments below. The most important addition is that
"pending instruction" needs a defined state machine and schema, otherwise the same hindsight P&L returns
through another field.

## Defects in current code that the plan must cover

1. **Realized P&L is fabricated from touches** (`two_tranche_exit_model.py` L195-233, L268-290).
   - The target realizes at `t1_target` when `peak >= t1_target`.
   - The stop realizes at `active_sl` when `ltp <= sl` or `tick_low <= sl`.
   - The SL-M branch realizes at `min(sl, low) - slippage`, which is a modeled price, not a fill.
   - EOD realizes at `ltp`.
   - A peak or low touch says nothing about queue position or fill. This conflicts with Rule 4 (no deterministic fills).
2. **Stop and trailing logic uses hindsight.** `peak_price` and `tick_low` are session extremes with no timestamps.
   - Order of events is unknown, so the code cannot tell whether the stop or the target came first.
   - Currently the stop branch is checked before the target branch, so one candle that spans both silently books the stop.
   - The breakeven trail arms from `peak` and can be applied retroactively to a low that came earlier.
3. **The default is misrouted.** The screener defaults to `SL_M` for NSE (L347). The Phase 1 requirement is a conservative
   `SL_LIMIT` default, so this must flip.
4. **Unknown types fall into the SL-M branch.** `else` at L212 and L285 treats any non-`SL_LIMIT`/`SL_L` string as SL-M, including
   typos such as `"SLM"`. The screener does the same at L353.
5. **Validation is incomplete and inconsistent.**
   - `isinstance(val, (int, float))` accepts `bool` (`True` passes as 1).
   - `math.isnan` does not reject `inf`.
   - `allocate_tranches` never validates `total_shares` type. A float or bool passes `<= 0` and then `math.ceil` and `math.floor` run on it.
   - `update_state` validates nothing (`ltp`, `peak`, `pdl`, `daily_atr`, `tick_low`, `limit_offset_pct`, `slippage_pts`).
   - `limit_offset_pct` and `risk_budget_rs` are unbounded. A negative offset puts the limit above the trigger.
   - `daily_atr` and `pdl` are only checked `> 0` (inf passes).
   - `max_notional_rs` and the `dtv` cap can produce `qty == 0` and then flow on with `tranche_allocation=None`. That is fine, but it must stay explicit.
6. **Trigger and limit are conflated.**
   - `stop_price` is used as both. The limit is recomputed inside `update_state` from `limit_offset_pct`, so sizing and state can disagree.
   - `allocate_tranches` is called with `effective_exit_price` (the limit) as `stop_price`, so `initial_stop` in the allocation is the limit, not the trigger.
   - `t1_target` is derived from the limit-based risk, while the screener's `target_price` uses the trigger-based `stop_price`. These are two different targets for one trade.
7. **Resurrection paths still exist.**
   - `TERMINAL_TRANCHE_STATES` protection depends on the caller passing `current_state`, and the radar never does (L591-597, no `current_state`). Every tick is stateless, so a stopped position reappears as ACTIVE.
   - `UNFILLED_TRIGGERED` may not be in the terminal set. If it is not, it flips back to `ACTIVE` on the next tick.
   - `TRAILED_BREAKEVEN` is re-derived from `peak`, which is fine only if `peak` is persisted.
8. **Radar treats state as display only.**
   - `except Exception: two_t_dict = None` swallows failures silently.
   - `max_p = ltp` when there is no peak.
   - A missing `is_cas` defaults to `False`, which gives the later 15:20 cutoff. That is the unsafe direction and should fail closed to the earlier one (15:10).
9. **Radar and model disagree.** The radar's own `state_lbl` (L567-581) and the model each run a separate stop and trail implementation.
   The radar's `elif peak >= risk` precedes `ltp <= initial_sl`, so a position that hit its stop shows as "TRAILED_TO_BREAKEVEN_PROTECTED".
10. **Qualification.** The scanner sets `qualified=True` by default when checks pass (L322). The radar accepts `surv_report["qualified_symbols"]`
    and treats a stale snapshot as readable (L294 comment). No evidence-age or as-of gate is present.

## Requirements (amended plan)

### R1. Numeric validation (one shared helper)
- Accept only `int` or `float` and reject `bool` (check `type(x) is bool` first). Require `math.isfinite`.
- Sizing inputs: `entry`, `stop`/`or_low`, `atr14`, `dtv` and `risk_budget` must be `> 0`. `max_notional` must be `> 0`.
  `limit_offset_pct` must be `>= 0` with a sane upper cap (for example `<= 5`). Portfolio inputs (`ltp`, `peak`, `tick_low`, `pdl`, `atr`) must be finite and `> 0`.
  `slippage_pts` must be `>= 0`. Shares must be a real `int`, not `bool`, and `> 0`.
- Reject NaN, inf, negative, bool, and string numerics. Normalize with `float()` only after the type check.
- Portfolio records, including persisted JSON: validate `shares`, `entry`, `initial_sl`, `risk`, `target`. `risk == entry - initial_sl` must hold within tolerance.
- Invalid input returns a rejected or invalid-state object with a reason code. It never raises out of the radar loop and is never rendered as valid.
- Add `peak >= ltp` and `low <= ltp <= high` sanity checks. A quote that violates them is a data fault, not a signal.

### R2. Execution type
- Allowed set is `{"SL_LIMIT", "SL_M"}` (alias `SL_L` maps to `SL_LIMIT`, and `SLM` and `SL-M` need an explicit decision).
- `None` or a missing value maps explicitly to `SL_LIMIT`, and the result records `exec_type_defaulted=True`.
- An empty string, whitespace, a non-str value or an unknown string raises or rejects. It must not fall through to SL-M.
- Do not switch on the exchange. The current NSE-to-SL_M default is removed.
- A legacy persisted record with no type field is "missing", so it defaults to SL_LIMIT (compatibility). A record with a bad type value is rejected.
  Both cases are logged.
- SL-M stays a valid opt-in. It still produces a pending instruction, never a fill at `min(sl, low) - slippage`.

### R3. Trigger vs limit stored separately
- Persist `trigger_price`, `limit_price` (None for SL-M), `limit_offset_pct` and `exec_type` as distinct fields.
- Do not recompute the limit inside `update_state`. Read the persisted limit.
- Constraint: for a sell stop, `limit_price <= trigger_price`, and both must be tick-aligned (NSE tick 0.05, so rounding is to `0.05`, not `round(x, 2)`).
- Sizing risk per share must use the worst case (`entry - limit`) as it does today. The tranche `initial_stop` should carry the trigger, and the limit should be a separate field.
  Make one target definition from one risk basis and state which.
- The `UNFILLED_TRIGGERED` gap test (`eff_low < limit_p`) is only a heuristic for "limit was never reached". Keep it out of realized numbers (see R4).

### R4. Touches become pending instructions with zero realized profit
- Target touch, stop touch and EOD square-off produce a `PENDING_*` instruction: `PENDING_TARGET_EXIT`, `PENDING_STOP_EXIT`, `PENDING_EOD_SQUAREOFF`.
  Each carries: instruction id, tranche, shares, `trigger_price`, `limit_price`, `exec_type`, reason, and the evidence timestamp/source of the touch.
- `t*_realized_pnl` and `total_realized_pnl` are `0.0` until Phase 2 supplies a fill ledger. Add `realized_source = "NONE_PENDING_FILL_LEDGER"` so nothing downstream reads zero as "flat".
- Unrealized and MTM stay computed off `ltp`. The label must say so, and a pending stop should show worst-case exposure, not a realized loss.
- Rename outputs so nothing says `*_FILLED`, `STOPPED_OUT`, `CLOSED_PROFIT` or `TARGET_FILLED` (tests and dashboard included). Use a `hypothetical_pnl_if_filled` field only if clearly labeled and excluded from totals.
- Ordering: with only OHLC/peak/low, when both target and stop are touched in one window, the order is unknown. Emit an `AMBIGUOUS_ORDER` pending pair and never assume target-first or stop-first.
  For risk-safety, honor the stop instruction and mark the target unverified.
- `is_eod_squareoff` must not set the tranche to `CLOSED_MIS_SQUAREOFF`. It issues `PENDING_EOD_SQUAREOFF` while the position remains open in MTM.
- T2 de-risk trailing (`t1_derisked`) currently keys off `TARGET_FILLED` and `CLOSED_MIS_SQUAREOFF`. Those become unverified, so T2 must not trail off a T1 that has not been confirmed. Use `peak >= entry + 1R` only for tighter stops (conservative), never as a claim of banked profit.
- Radar output (L555-611): `mtm_pnl` remains, but the aggregate must not be labelled realized. The `active_sl = ltp` EOD override is misleading and should go.

### R5. Persistence and no resurrection
- Persist pending state (atomic write: temp file, fsync, `os.replace`; include a schema version and a monotonic sequence or hash).
- Key it by `(session_date, symbol, tranche, instruction_id)`.
- A pending or terminal state is monotonic. Only a fill-ledger event (Phase 2) or an explicit operator-audit action may transition it.
  No price tick may move a `PENDING_*` back to `ACTIVE`, and a new higher `ltp` must not cancel a pending stop.
- The radar must load prior state and pass it as `current_state`. Today it does not, so terminal protection is inert (see defect 7).
- Persist `peak` and `low` as running extremes. Peak only rises and low only falls, and both are session-scoped, so a new date resets the run.
- Corrupt or unreadable state fails closed: freeze that symbol, do not recreate it as ACTIVE. Never auto-delete the file.
- Treat `UNFILLED_TRIGGERED` or its replacement as terminal-until-ledger.
- Write concurrency: the radar and the kite bridge could both write. Use one writer or a lock file.
- Restart mid-session must reproduce the same state, and a test should cover restart-after-pending.

### R6. Freeze qualification and quarantine historical claims
- Add a single switch (config or constant, default frozen). While frozen, `qualified=False` for every candidate, with `qualification_block_reason="PHASE1_FREEZE"`.
  `surv_report["qualified_symbols"]` must not be used to open positions.
- Apply the freeze on all readers: the scanner, the radar, the bridge, and any script writing `shared/track2_liquid/` logs.
- Quarantine, do not delete: move or tag historical entries (e.g. `shared/track2_liquid/03_TRADE_LOG.md`, `monday_orb_paper_template.csv` rows, any "banked", "target filled", win-rate or expectancy claims) with `verification_status=UNVERIFIED_QUARANTINED`.
  Copy to a `quarantine/` folder or add a marker column. Deleting is prohibited by AGENTS.md.
- The Rule 1 gate requires 60 sessions and 20 fillable entries with verified positive expectancy. Quarantined rows must not count toward either number, and the counter should be recomputed from verified rows only (currently zero for Track 2).
- Keep Track 1 isolated. Do not touch `CHATGPT/observation_log.csv` or `shared/track1_esm/`.
- Add a test that fails if any quarantined record is read by the gate counter.

### R7. Research radar defaults
- `qualified` defaults to `False` at construction and only a verified path can set it. The scanner's `qualified=True` at L322 must be gated.
- The radar payload must carry `research_only=True`, `qualified=False` and `evidence_asof` (a timestamp) on every candidate.
- Stale or hindsight evidence rejected: require each input (pre-open snapshot, surveillance report, circular, F&O membership, regime) to have an `as_of` timestamp and a maximum age, tied to the session date.
  Data stamped after the decision time (for example a post-09:30 high used to decide a 09:30 breakout) is hindsight and is rejected.
  Missing timestamps count as stale.
- Replace the "readable even when the snapshot is stale" behaviour (radar L294) with a hard rejection plus a visible reason code.
- Fail-closed surveillance flags remain: `is_surveillance is False` and `is_fno_underlying is True`, both explicit and non-null.
- Rules order (Rule 10) is unchanged. This freeze sits above Rule 7 entries, and Rule 6 exits still take priority for existing paper positions.

## Missing requirements / risks the plan does not mention
- **Tick size and price bands.** Rounding with `round(x, 2)` can produce a price that is not a multiple of the tick (0.05 for most NSE EQ, 0.01 for some). A non-aligned trigger or limit would be rejected by the exchange.
- **Timestamps on all evidence.** `ltp`, `peak` and `tick_low` come without times. Ordering, staleness and hindsight checks all depend on them.
- **Schema and back-compat.** Existing tests (`tests/test_track2_v2.py`, modified but uncommitted) assert `TARGET_FILLED`, `STOPPED_OUT` and `t1_realized_pnl == 19 * ...` (also the `__main__` asserts at model L358-360). They must be rewritten as pending semantics, not deleted or weakened. Keep a compatibility read path for old JSON.
- **Dashboard and log writers.** Anything rendering `total_realized_pnl` or writing "TARGET_FILLED" to `03_TRADE_LOG.md` must be found by grep and updated (`render_terminal_dashboard`, `track2_kite_bridge`, `exchange_circular_poller`).
- **Rule 5/9 crossover.** Track 2 sizing uses a 0.1% DTV cap in code but Rule 9 states 15% participation. Rule 11 says Track 2 has its own rules, so decide explicitly which applies and document it. Do not silently change the cap in Phase 1.
- **Gap-through-stop on Track 2.** Liquid F&O names can gap. `UNFILLED_TRIGGERED` handling should model the risk without importing the Track 1 10-day lockout (Rule 11).
- **Zero-share sizing.** A `qty == 0` rejection must be visible (`constrained_by`), and never turn into an "OK" result with 0 shares.
- **Exceptions swallowed.** Replace `except Exception: two_t_dict = None` with a typed error and a logged reason, and surface `two_tranche_error` in the payload.
- **Track isolation.** Nothing in this phase may import Track 1 circuit or LC logic, or write to Track 1 storage.
- **Paper gate.** No broker call, no order placement. Pending instructions are records only.
- **Rule 8 peer review.** This file is Claude's pre-implementation review. Post-implementation review is still required before core-model changes are treated as accepted.

## Suggested acceptance tests
1. Reject `True`, `inf`, `nan`, negatives, strings and `-0.0` for every numeric parameter listed in R1.
2. Exec type: `None` gives `SL_LIMIT` with a defaulted flag, `"BOGUS"` is rejected, and an NSE symbol without an explicit type does not get SL_M.
3. A trigger-only touch yields a `PENDING_STOP_EXIT` with `realized == 0` and `realized_source` set.
4. A target touch yields `PENDING_TARGET_EXIT` with `realized == 0`.
5. A single bar spanning both target and stop yields `AMBIGUOUS_ORDER`, and no realized profit is booked.
6. EOD yields `PENDING_EOD_SQUAREOFF`, not a closed state.
7. Feed a stop touch, then a rising `ltp`. State stays pending (no resurrection). Repeat across a process restart.
8. A corrupt state file freezes the symbol and does not create ACTIVE.
9. Frozen qualification: every candidate has `qualified=False` and the block reason is present. The stale-evidence and future-timestamp cases are rejected.
10. Quarantined rows do not count toward the 60-session and 20-entry gate.
11. Limit is never above trigger. The persisted limit is read and not recomputed.

## Requests back to the implementer
- Post the proposed pending-state schema and transition table for review before coding R4 and R5.
- Confirm the treatment of `SLM` aliases and the stance on `UNFILLED_TRIGGERED` (fold into `PENDING_STOP_EXIT` with a `gap_through_limit` flag is my recommendation).
- Confirm that quarantine is a move/tag, not a delete.
