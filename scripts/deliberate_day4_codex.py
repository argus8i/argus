"""
scripts/deliberate_day4_codex.py
================================
Proactive Tri-Agent Bus Deliberation for Sprint Day 4 Architecture & Build Plan.
Per Rule 8 v2 Invariant 5 (Mandatory Bus Deliberation Invariant, Yashu 1 Oct 2026).
"""

import sys
import json
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from antigravity.daemons.tri_agent_bus import ask_codex_detailed

PROMPT = """Signed Nexus deliberation request for OpenAI Codex (Senior Systems, Execution-Reality & Reliability Engineer).

Author: Antigravity (Quantitative Modeling & Infrastructure Orchestrator)
Subject: Sprint Day 4 Build Plan & Architecture Deliberation (Rule 8 v2 Invariant 5)
Branch: feature/day4-backtest-and-stress-testing
Commit Base: 565d5a8 (main tip with Days 1-3 approved & merged)

Codex, per Rule 8 v2 Invariant 5 (Mandatory Bus Deliberation Invariant), Antigravity is initiating cross-agent deliberation on the proposed architecture, data contracts, and verification methodology for Sprint Day 4: "Purged Walk-Forward Backtesting Engine, Multi-Tier Friction Hurdle, and Adversarial Regime Stress-Testing".

Here is the proposed design:

1. ARCHITECTURAL SCOPE & MODULE DESIGN:
   - Module: `antigravity/engine/backtest_engine.py`
   - Class: `WalkForwardEngine`, `PurgedFoldManager`, `BacktestSimulation`
   - Complete integration of Day 1 (`PreOpenEligibilityValidator` / `universe_daily.parquet`), Day 2 (`PortfolioRiskGovernor` with Adjusted A1 3-slot cap, Rs 38,000 slot cap, Rs 1,500 trade risk, Rs 114,000 exposure ceiling; `ExecutionSimulator` with discrete states, limit orders, gap-opens, and 15% volume participation cap), and Day 3 Alpha Strategies (`DeliveryAccumulationStrategy`, `High52MomentumStrategy`, `ExpiryReliefStrategy`).

2. PURGED ROLLING WALK-FORWARD FOLD STRUCTURE:
   - Point-in-time F&O universe data runs 2022-01-03 through 2026-09-24.
   - Warm-up requirement: Sleeve B (52-week high) requires 252 sessions warm-up, establishing January 2023 as the start of active multi-sleeve evaluation.
   - Fold 1: Train 2022 (warm-up / calibration) -> 10-day purge embargo -> Test 2023 (evaluation).
   - Fold 2: Train 2023 -> 10-day purge embargo -> Test 2024 (evaluation).
   - Untouched Holdout: 2025-2026 reserved as absolute untouched holdout benchmark.
   - Purge Embargo: Strict 10-session purge gap between train and test splits to guarantee zero multi-day swing position leakage across folds.

3. MULTI-TIER FRICTION HURDLE & SENSITIVITY:
   - Tier 1 (Zero Friction): Theoretical gross benchmark (0 statutory costs, 0 slippage).
   - Tier 2 (Realistic Baseline): Statutory costs (STT, NSE, SEBI, GST, Stamp Duty, Rs 15.93 flat DP charges on sell) + 7.5 bps slippage per side.
   - Tier 3 (Severe Stress): Statutory costs + 20 bps slippage per side.
   - Hurdle Invariants: Under Tier 2, system must pass: Profit Factor >= 1.30, Win Rate >= 45%, Net Expectancy > 0.25R.

4. ADVERSARIAL REGIME STRESS TESTS (`tests/test_day4_backtest.py`):
   - Regime 1: 2022 Global Bear Market grind (persistent sideways-down chop).
   - Regime 2: 2024 Election Volatility shock (04-June-2024 gap down and intraday reversal).
   - Regime 3: Rule 5 10-day Lower Circuit Lockout descent stress scenario.
   - Portfolio Drawdown Invariant: Maximum portfolio drawdown across all 3 slots must not exceed 6.0% (4R aggregate).

5. QUESTIONS FOR CODEX DELIBERATION:
   a) Do you agree with the 10-day purge gap specification and the fold boundaries (2022 warm-up -> 2023 test -> 2024 test, with 2025-2026 untouched holdout)?
   b) Are there specific edge cases in swing trade exit reconciliation (e.g. multi-day circuit locks, gap-down exits through stop loss) that you want explicitly enforced in `WalkForwardEngine`?
   c) Do you have any specific recommendations for the metric reporting contracts in `shared/track2_liquid/backtests/walk_forward_report.md`?

Please provide your technical feedback and any adversarial constraints you require us to build into Day 4.
"""

def main():
    print(f"[{time.strftime('%X')}] Dispatching Day 4 Deliberation Request to Codex via Nexus Bus...")
    res = ask_codex_detailed(PROMPT, timeout_sec=240, min_chars=100)
    print(f"[{time.strftime('%X')}] Codex Response Received (success={res.get('success')}, rc={res.get('returncode')}, elapsed={res.get('elapsed', 0):.1f}s):")
    print("=" * 80)
    print(res.get("output", ""))
    print("=" * 80)

if __name__ == "__main__":
    main()
