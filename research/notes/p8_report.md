# P8 report: shadow runner

**Branch:** `track2/decision-engine`
**Author:** Claude
**Date:** 26 Sep 2026 (Yashu's overnight mandate)
**Scope:** paper/research only. Nothing in `research/shadow/` can place, modify or cancel an order: `ShadowRunner(allow_live=True)` raises `LiveTradingRefused`.

## 1. What was built

| Module | Role |
|---|---|
| `research/shadow/run_day.py` | **`tick`** (each bar close + 60 s): validates the live file, keeps closed bars from strategy-eligible sources, runs the adapters for the day through `BacktestEngine.run(only_dates=[D])`, and appends new signals to a **hash-chained journal** stamped with the wall clock. A heartbeat is written every tick. **`close`**: final run, then allocation through `ShadowRunner`, E1 outcomes, SHADOW ledger rows, and the day report (`.md` + `.json`). A signal counts as prospective only if the journal holds it with the same side and prices within 5 minutes; otherwise it becomes `SHADOW_LATE` / `SHADOW_CHANGED` / `SHADOW_UNJOURNALED` with evidence E1_CF (never admissible). **`replay`**: a past session ticked bar by bar with a simulated clock. |
| `research/shadow/runner.py` | **`ShadowRunner`**: bar-close allocation of the emitted signals. Sizing is `size_qty` (₹1,500 risk; ₹38,000 at the worst admissible entry price) × the VIX multiplier; `allocator.allocate` runs in SHADOW or EXPLOIT mode; the `ExposureBook` holds the reservations; one position per weekly residual cluster and two per sector; simulated fills and exits are applied as book events in time order. Every emitted signal produces one ledger row, and the book must be flat at the close. |
| `research/shadow/feed.py` | `SnapshotReplayFeed` (point-in-time replay from the sealed history). **`UpstoxIntradayFeed`** replaces the 9-symbol `kite.zerodha.com/oms` bridge: it polls Upstox V2 intraday 1-minute candles (no login), resamples them with the history's own `resample_15m`, and writes `live_candles_track2_upstox.json` atomically with provenance `UPSTOX_API_V2`. `DhanIntradayFeed` refuses: it needs the paid Dhan Data API that Yashu declined. |
| `research/shadow/reconcile.py` | Per-session dispositions (slot / cluster / sector / aggregate / VIX size-zero / late), peak exposure against ₹1,14,000, whether the book was flat, and progress: rule 1 (60 sessions, 20 fillable entries), plan n_pre = 111 admissible (E2/E3), and the 85-trade power reference (orientation only). |

**Fail-closed inputs:**
- A live symbol whose `source_url` host is not an eligible provenance class is dropped (rule 1.2.11).
- A forming bar (ending after now − 60 s) is not used, and a file for another session is refused.
- History must reach the previous weekday unless the gap days are declared holidays. `trading_calendar.json` is **wrong on 5 holidays of 2026**, so it is not used.
- Calibration reads only post-CAS sessions before D (the holdout stays hidden, and 24-bar and 25-bar sessions are never mixed).
- The universe for D uses the previous F&O session's membership and D's ban list; a missing ban list makes every stock `FO_BAN_UNKNOWN`.
- VIX stale or missing gives m = 0 (no entry).
- An unknown cluster falls into one shared bucket (so at most one position).
- RESID_REV runs only from a valid lock, and only as its locked `holdout_variant`.

## 2. Tests (exact commands, 26 Sep 2026)

```
python -m pytest -q research/tests/test_p8_shadow.py research/tests/test_shadow_run_day.py
29 passed in 38.31s        (exit 0)

python -m pytest -q -rxX research/tests/test_p8_production_contract.py
2 passed, 6 xfailed in 1.01s   (the 6 strict xfails are the P8.1 production changes requested from Codex)
```

**What the tests prove** (from Yashu's list):

| Requirement | Test |
|---|---|
| Rule 1: `allow_live=True` is refused unconditionally | `test_allow_live_is_refused` |
| Zero look-ahead: truncating every bar after t gives identical decisions at t | `test_zero_lookahead_decisions_at_t_ignore_later_bars`; `test_replay_bar_by_bar_equals_one_shot_and_the_backtest` |
| Runner matches `BacktestEngine` emission and sizing | `test_runner_matches_engine_emission_and_sizing` (every signal present; `qty_planned` identical at m = 1) |
| Adjusted A1: ₹38,000 per slot, ₹1,14,000 aggregate including pending | `test_adjusted_a1_slots_and_caps_with_pending_reservations` |
| Stale or missing VIX fails closed | `test_missing_or_stale_vix_means_no_entries`; `test_high_vix_scales_quantity_down` |
| Unknown cluster fails closed | `test_unknown_clusters_share_one_bucket_fail_closed` |
| Late or unjournaled signals are never admissible | `test_a_signal_journaled_late_is_never_admissible`; `test_reconcile_labels_every_case` |
| Forbidden sources, forming bars and bad bars are dropped | `test_live_file_drops_forbidden_sources_and_forming_bars`; `test_a_bad_bar_drops_the_symbol_for_the_day` |
| Upstox feed output is valid | `test_upstox_intraday_feed_writes_a_valid_live_file`; `test_dhan_feed_refuses` |
| Close allocates through the runner and the ledger is append-only | `test_close_allocates_through_the_shadow_runner`; `test_close_writes_shadow_ledger_rows_once`; `test_runner_rows_append_to_the_ledger` |
| Reconciliation | `test_reconcile_counts_progress_and_drop_reasons`; `test_runner_portfolio_aggregates_sessions_under_a1` |

## 3. Replay on real data

`python -m research.shadow.run_day replay --date 2026-09-24` (real Upstox history; ORB_PROD only, because RESID_REV was not locked at the time):

- **25 ticks, 19 signals, 19 of 19 PROSPECTIVE; the bar-by-bar journal is identical to a one-shot run.**
- 312 symbols used, 205 eligible, 0 dropped.
- Wall time 2 min 9 s, about 5 s per tick, well inside the 60 s decision delay.

## 4. What still blocks a live shadow run (not code in this branch)

1. **Feed.** Run `UpstoxIntradayFeed.poll` every bar, or fix the production bridge.
   - At the polite 1 request/s, ~190 instruments take ~3 minutes per poll, so decisions land about 4 minutes after the bar close. That is inside the 5-minute journal window.
   - Lowering the pacing needs Upstox's published limits verified first (ASSUMPTION; floor 0.25 s).
2. **Daily refresh after each close (Antigravity).** Upstox 1-minute data for the session, then `upstox_history build`; the CM and F&O bhavcopy; the next session's ban list. Without them, `HISTORY_STALE` / `FO_BAN_UNKNOWN` block everything, by design.
3. **Scheduling.** A task at each bar close + 60 s (`tick`) and one after 15:30 (`close`, then `reconcile`).
4. **Production patches (P8.1)** for Codex: `shared/reviews/track2_decision_engine_review_request_2026-09-26.md`.
5. **Strategies to shadow.** ORB_PROD is `KILLED_ON_DESIGN`. RESID_REV's status comes from the holdout (P7 report, Part B). A killed or rejected strategy can still be shadowed in SHADOW mode for evidence, but EXPLOIT allocates it nothing.
