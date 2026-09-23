# Claude post-implementation review: Track 2 Phase 1 (20 Sep 2026)

Reviewer: Claude (red-team). Read-only review; no source edits. Scope: contract
`codex_phase1_transition_contract_20260920.md` plus the diffs of `two_tranche_exit_model.py`,
`liquid_momentum_screener.py`, `track2_live_radar.py`, `track2_universe_scanner.py` and
`03_TRADE_LOG.md`. I did not re-run the 283 tests and I did not read
`tests/test_track2_phase1_reliability.py` in detail. I make no claim that Phase 1 is accepted.
Raw source provenance, a point-in-time radar and a durable fill ledger remain blocked.

## Verdict on "is pending persistence honest?"

Partly. The model preserves pending, partial and cancel states only if the caller passes
`current_state`. Nothing in the repo persists or passes it:

- The radar builds `two_t` without `current_state` (`track2_live_radar.py` ~L585-596).
- The radar also passes no `tick_low`.
- No ledger exists.

The contract line "pending states preserved, no resurrection" therefore holds within one
call chain, not across polls. Today this is masked because `positions_cfg = []`. The
"pending" label is honest as a research intent. It is not honest as durable state. The
docstring and contract should say "preserved only when caller supplies prior state; no
persistence exists". Disabling historical positions is the correct interim choice.

## Important defects

1. **Likely NameError, `audit_daily_surveillance` (`track2_live_radar.py` L271).**
   It now calls `ExchangeCircularPoller(...)`, but the import block (L46-52) does not import
   it. My grep of the file finds only the use site.
   - Effect: the radar's pre-open surveillance step raises before any output, and
     `scan_session` depends on `surv_report["qualified_symbols"]` (L283).
   - This is a regression from the previous hardcoded path. Add the import, or confirm it is
     imported elsewhere.
   - Add a test that calls `audit_daily_surveillance` un-mocked. The 283 passing tests
     evidently do not.

2. **Pending state does not survive polls (see verdict).**
   - A stop touch at poll N followed by a recovery at poll N+1 returns to
     `ACTIVE_INITIAL_STOP` unless the caller round-trips state.
   - The radar also passes no `tick_low`, so wick-only stop touches are invisible. Only
     `ltp <= stop` is seen.
   - Both matter the moment positions are re-enabled. Make `current_state` mandatory once a
     ledger exists.

3. **`AMBIGUOUS_ORDER` label can be misleading.** It is computed from the current
   observation only.
   - Example: T1 was already `PENDING_STOP_EXIT`, then a later target touch occurs. The
     status is preserved, but the composite says `AMBIGUOUS_ORDER`.
   - Conversely, a prior pending status hides a genuine new conflict. Ambiguity should be
     derived from the preserved statuses plus the new touch, or reported per tranche.

4. **Sizing risk and tranche risk are inconsistent (`liquid_momentum_screener.py`).**
   - Share count uses `risk_per_share = entry - effective_exit_price` (limit price, offset
     0.5%).
   - `allocate_tranches` is now given `stop_price`, so tranche `risk_per_share` and the 1.5R
     target use the structural stop.
   - The reported `risk_reward_ratio` and the tranche target therefore use different R
     definitions. The 2R `target_price` in `SizingResult` is a third target next to the
     tranche T1 at 1.5R.
   - Not wrong if intended, but it must be documented. Otherwise "1R" differs across
     outputs.

5. **Strict `type(v) in (int, float)` rejects `numpy.float64` and `numpy.int64`.**
   - This affects sizing inputs, `allocate_tranches` and `update_state`.
   - Any pandas- or numpy-sourced price or ATR fails closed as `INVALID_OR_NAN_INPUT`.
   - It is safe but can silently zero every candidate. Coerce with `float()` after an
     `isinstance(v, numbers.Real)` and not-bool check, or add a test that feeds numpy values.

6. **Dead or misleading parameters in `update_state`.**
   - `order_type`, `limit_offset_pct` and `slippage_pts` are validated but never used.
   - `order_type=None` is accepted here, whereas the contract says the default is SL_LIMIT
     and unknown values are rejected.
   - The `"SL_L"` branch in `calculate_position_size` is unreachable because the validator
     already rejects it. The contract says aliases are rejected, so remove the branch.

7. **Portfolio-capacity check (`check_portfolio_risk_capacity`).**
   - `existing_sector_counts` defaults to `None`, so omitting it always returns
     `INVALID_OR_MISSING_PORTFOLIO_INPUT`. That is fail-closed but a signature trap.
   - Sector keys are not normalised. The value is `.strip()`-checked but looked up raw, so
     `"Banks"`, `"banks"` and `"Banks "` can bypass the concentration limit.
   - Boundary rounding is trivial: `round(...,2)` can admit up to 0.005 over the cap.
   - `ASSUMED_*` class constants are unused; the defaults are hard-coded literals.

8. **`RESEARCH_ORB_HYPOTHESIS` is not handled by the console renderer.** In the renderer
   (L679-686) it falls to the red `else` branch. Cosmetic, but red will read as an error.
   Also confirm no downstream consumer (Kite bridge, dashboards) still keys on
   `BUY_ORB_CONFIRMED`. `append_session_summary_to_logs` (L748) will now find zero triggers,
   which is the desired freeze.

## Verified as correct or acceptable

- **Rule 9 vs Track 2 (as asked).** Track 2 keeps its own 0.1% of median DTV cap
  (`liquid_momentum_screener.py` L401-404: `floor(0.001 * dtv_rupees / entry)`). It does not
  reference the Track 1 15% participation formula. Rule 11 isolation is intact.
- **Rounding and math.**
  - The DTV cap uses floor, so it can only reduce size.
  - `actual_risk = qty * risk_per_share` uses the limit-inclusive risk, which is conservative.
  - `isfinite` and `> 0` checks replace NaN-only checks, which closes `inf`.
  - `bool` inputs are rejected because `type(True)` is `bool`.
  - `allocation != expected` catches mutated dataclasses.
  - `total_shares` must be exactly `int`.
- **No status resurrection within a call chain.** Legacy terminal states
  (`TARGET_FILLED`, `STOPPED_OUT`, `CLOSED_MIS_SQUAREOFF`, `REJECTED`) fail the
  `RESEARCH_STATES` membership check and raise. Any nonzero realized PnL in the prior state
  raises. Corrupt or mismatched prior state raises rather than resetting to active.
  `UNFILLED_TRIGGERED` stays pending.
- **Realized P&L booking removed.** `total_realized_pnl` is always 0 and
  `realized_source = NONE_PENDING_FILL_LEDGER`. Trailing from OHLC extremes is gone.
- **Qualifier output paths.**
  - The radar payload sets `qualified=False`, `qualification_eligible=False`,
    `verified_sessions=0` and `verified_fillable_entries=0`.
  - The scanner emits `candidates: []`, `total_qualified: 0` and moves names to
    `research_candidates` with `qualified=False`.
  - The silent canonical-fallback injection is removed. `is_canonical_fallback` is now a
    constant `False`; drop the field or keep it explicitly as deprecated.
  - `SizingResult.qualification_eligible` defaults to `False`.
  - The trade-log hold banner correctly supersedes the conflicting 3/60 vs 4/60 and 3/20 vs
    6/20 counters, and keeps history.
- **Stale surveillance.** The scanner no longer fabricates `checked_at` or defaults
  `asm_stage`/`gsm_stage` to 0. Missing values now fail closed in the monitor (checked
  `track2_surveillance_monitor.py`).
- **CAS/non-CAS cutoffs in the radar** are conservative: CAS is assumed unless
  `is_cas is False`. The cutoffs are sourced only to a broker bulletin, so they are
  provenance-pending. They are also currently dead code.

## Recommended before Phase 2

1. Fix the import in item 1 and add an un-mocked `audit_daily_surveillance` test.
2. Add a numpy-input test and decide on coercion (item 5).
3. Document or align the R definitions (item 4).
4. Make persistence explicit: a required `current_state` with a ledger-backed loader, plus
   `tick_low` from real minute data.
5. Remove dead parameters and the `SL_L` branch, and normalise sector keys.

Unresolved dissent: none. Dependencies outside this review: raw circular provenance
(Antigravity) and a point-in-time feed.
