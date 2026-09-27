# P0 baseline (25 Sep 2026)

## Main checkout (read-only, `C:\Users\yashw\swing trades`)

- Branch `claude/institutional-backtest-derivatives`, HEAD `cdc0665` ("feat(data): add DhanHQ historical 15m candle downloader…").
- `git status`: one untracked file only, `Claude outputs/2026-09-25_track2_execution_plan_for_claude_code.md` (this plan). `git diff --stat`: empty.
- The plan expected "many uncommitted modifications, including AGENTS.md". At 12:48 IST there were none. Nothing in the main checkout was changed.

## Worktree

- `C:\Users\yashw\swing-trades-track2`, branch `track2/decision-engine`, created from `cdc0665`.
- **It already existed when P0 started** (created 12:46 IST, clean, no commits of its own). I did not create it. It matches the plan's path and branch name, so it is used as is.
- The first commit is `1aa0e31`. It ignores `shared/track2_liquid/history/` and `research/outputs/` and was made before anything was generated.
- Python: `C:\Users\yashw\swing trades\.venv\Scripts\python.exe`.
- The plan is copied to `research/notes/PLAN.md` (`0bcd087`), byte-identical (SHA-256 prefix `29eefc3dced72bbb`).

## Tests

`python -m pytest research/tests -q -p no:cacheprovider` from the worktree root on Windows gives **314 passed**, exit 0 (MEASURED, 25 Sep, HEAD `cdc0665`). The plan's 306 predates 3bc73a4 and cdc0665, which added tests.

## Section 2 claims, checked at `cdc0665` (the plan checked them at `3bc73a4`)

| Claim | Holds? | Evidence |
|---|---|---|
| `is_index_symbol` is a "NIFTY" substring test, so `INDIA VIX` is treated as a stock | yes | `is_index_symbol('INDIA VIX') = False` |
| Research round trip on ₹583.33 × 100 is ₹61.99 (0.1063%) | yes | `DhanFeeEngine.calculate_order` |
| Production `calculate_transaction_costs` uses 0.00297% and gives ₹61.85 | **no** | Commit `9e712ed` (after the plan) changed production to 0.0030699% and added SEBI fees to the GST base. Production now also gives **₹61.99**. The circular reference (FA73061) is still unverified. |
| `calculate_order` raises on a zero-quantity fill | yes | `ValueError` |
| `two_tranche_breakeven_hurdle(0.074, q)` gives 0.6137 / 0.5653 / 0.4296 | yes | computed |
| `friction_in_r` defaults to ₹61.86 | yes | signature default |
| Six original adapters are stubs, plus four 3bc73a4 stubs, all returning `NO_PATTERN` | yes | `research/backtest/strategies.py` |
| `StrategyContext` exposes `store` | yes | `strategies.py:26` |
| The engine is long-only (`_assess_intent`), counterfactual qty falls back to 100, and there are `_process_bar_for_trade`, `_close_rms_squareoff`, `_finalize_trade` | yes | `research/backtest/engine.py` |
| `var_elm_rate=None` rejects everything as `REJECTED_GOVERNOR_MISSING_MARGIN_RATE` | yes | `engine.py:324` |
| `universe.check` is a linear scan | yes | `universe.py` |
| The 32-session file has 24 bars per session for stocks and 25 for NIFTY, and 32 daily bars | yes | loaded and counted |
| dhanhq 2.2.0 is in `.venv` | yes | `importlib.metadata` |
| pyarrow is available | **no** | Not installed. It is only needed from P3; installing it downloads a package, so I will ask at the P2 stop. |

## Provenance of the Kite file

`shared/track2_liquid/historical_candles_track2.json`, symbol BDL:
- `source_url` = `https://kite.zerodha.com/oms/instruments/historical/548865/15minute?from=2026-08-09&to=2026-09-23`
- `daily_source_url` = `.../548865/day?...`

This is Zerodha's web-session OMS endpoint, not a licensed API. It must never be used to refresh data (plan rule 1.2.11).

## Repo facts that contradict the plan

1. **Production fees.** They are already 0.0030699% (₹61.99) at HEAD, not 0.00297% (see above).
2. **`.gitignore` hides locks.** It ignores `*.lock` globally, so `research/studies/prereg/resid_rev_v1.lock` (P7.3) would never be committed. `1aa0e31` adds `!research/studies/prereg/*.lock`, checked with `git check-ignore`.
3. **Extra work already exists.** `cdc0665` (Antigravity) already adds `research/data/dhan_historical_fetcher.py` and its tests. P3 must build on it or reconcile with it, not duplicate it.
