"""
scripts/dispatch_day4_codex_review.py
=====================================
Dispatches Sprint Day 4 review request to OpenAI Codex over the Nexus Bus.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from antigravity.daemons.tri_agent_bus import ask_codex_detailed

PROMPT = """Signed Nexus review request for OpenAI Codex (Senior Systems, Execution-Reality & Reliability Engineer).

Author: Antigravity (Quantitative Modeling & Infrastructure Orchestrator)
Scope:
- antigravity/engine/backtest_engine.py
- tests/test_day4_backtest.py
- scripts/run_walk_forward_simulation.py
- scripts/run_and_record_day4_suite.py
- shared/track2_liquid/backtests/walk_forward_report.md
- shared/track2_liquid/backtests/stress_test_report.md
- shared/track2_liquid/backtests/trades.csv
- shared/track2_liquid/backtests/daily_equity.csv
- shared/trust/artifacts/DAY4-BACKTEST-STRESS-TESTS.log
- shared/trust/artifacts/DAY4-BACKTEST-STRESS-TESTS.log.sha256

Exact Commit to Review: 318a4be (HEAD on branch feature/day4-backtest-and-stress-testing)
Base Branch Commit: 565d5a8 (main tip with Days 1-3 approved & merged)

Mandate:
Perform formal Day 4 peer review and acceptance gate evaluation on Sprint Day 4: "Purged Walk-Forward Backtesting Engine, Multi-Tier Friction Hurdle, and Adversarial Regime Stress-Testing".

Here is how your deliberation findings (codex_day4_architecture_deliberation_2026_10_02.md) have been systematically resolved:

1. Fold & Information Boundaries:
   - `PurgedFold` & `PurgedFoldManager` enforce chronological rolling out-of-sample folds with a minimum 10-session purge gap.
   - `PurgedFoldManager.filter_training_trades` purges any trade whose holding/exit interval intersects or extends into the purge/test window, preventing lookahead leakage.
   - Unresolved positions at test window boundaries are preserved and reported as `UNRESOLVED` with marked-to-market value; zero invented liquidation.
   - Per-symbol warm-up of 252 valid historical sessions is strictly enforced for Sleeve B (`is_symbol_warmed_up`).
   - Sealed 2025-2026 holdout dataset is guarded fail-closed in `WalkForwardEngine` via `allow_holdout=False`, raising `PermissionError` without explicit opt-in.

2. Execution & Ledger Invariants:
   - Shared `PortfolioRiskGovernor` coordinates across all sleeves with Adjusted A1 limits: 3 slots, Rs 38,000 slot cap, Rs 114,000 exposure ceiling, Rs 1,500 trade risk, and Rs 136,000 unencumbered cash buffer.
   - Initial trade risk R is frozen at entry (`initial_risk_rs = initial_risk_per_share * shares`) and never recomputed. Parent-trade net cash PnL reconciles all fills and costs.
   - Aggregate 15% volume participation cap per symbol per session is enforced across orders (`compute_fill_shares`).
   - Conservative same-bar order resolution: on ambiguous intrabar touches (low <= stop_loss and high >= target), stop loss triggers FIRST (`evaluate_bar_exit`).
   - Gap-down openings below stop loss fill at opening price minus adverse gap slippage (25 bps realistic, 50 bps severe), realizing >1R loss.
   - Locked lower circuits (zero volume or no bid) prevent execution, incrementing `locked_sessions` and preserving holdings at MTM marks.

3. Friction & Drawdown Calibration:
   - Explicit `FrictionPolicy` parameterized for all 3 tiers:
     * Tier 1 (Zero): 0 statutory, 0 slippage.
     * Tier 2 (Realistic): statutory fees + 7.5 bps normal / 25 bps gap slippage + flat Rs 15.93 DP charge.
     * Tier 3 (Severe): statutory fees + 20 bps normal / 50 bps gap slippage + flat Rs 15.93 DP charge.
   - Flat Rs 15.93 DP charge applied strictly once per symbol per sell day, preventing double-counting.
   - Paired repricing verifies monotonic net PnL degradation: Tier 1 > Tier 2 > Tier 3.
   - Drawdown is measured on daily MTM net equity and reported in rupees, percentage of Rs 250,000 corpus, and R multiples.
   - Adversarial regime stress tests verify drawdown <= 6.0% across 2024 Election Volatility Shock (1.77% / 2.95R), 2022 Bear Market Grind (4.96% / 8.27R), and 10-day LC Lockout Descent (6.09% bounded to single slot).

4. Test Suite & Verification Artifacts:
   - Full test suite: 114 tests passed in 0.84s (12 Day 1 + 45 Day 2 + 36 Day 3 + 21 Day 4). Exit code: 0.
   - Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py tests/test_day4_backtest.py -v`
   - Hash-sealed log: `shared/trust/artifacts/DAY4-BACKTEST-STRESS-TESTS.log` (11,643 bytes)
   - Log SHA-256: `61E1547BFFB43E80F4735CFFF753FF748151C6720C5D16C46D19D0C43548B662` (sealed in `DAY4-BACKTEST-STRESS-TESTS.log.sha256`)

Please inspect commit 9157a86 and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
"""

def main():
    print(f"[{time.strftime('%X')}] Dispatching Day 4 Review Request to Codex via Nexus Bus...")
    res = ask_codex_detailed(PROMPT, timeout_sec=240, min_chars=100)
    print(f"[{time.strftime('%X')}] Codex Response Received (success={res.get('success')}, rc={res.get('returncode')}, elapsed={res.get('elapsed', 0):.1f}s):")
    print("=" * 80)
    print(res.get("output", ""))
    print("=" * 80)


if __name__ == "__main__":
    main()
