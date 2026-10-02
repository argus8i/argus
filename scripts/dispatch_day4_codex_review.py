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
- shared/trust/artifacts/test_codex_day4_9157a86_review.py
- shared/trust/artifacts/test_codex_day4_ee58cb3_review.py

Exact Commit to Review: 7c23f6c1cd023e3e5cd67a13f44f7438beb32373 (HEAD on branch feature/day4-backtest-and-stress-testing)
Base Branch Commit: 565d5a8 (main tip with Days 1-3 approved & merged)
Prior Review Reference: shared/trust/codex_day4_ee58cb3_independent_review_2026_10_02.md (CHANGES_REQUIRED on ee58cb3)

Mandate:
Perform formal Day 4 peer review and acceptance gate evaluation on replacement commit 7c23f6c for Sprint Day 4: "Purged Walk-Forward Backtesting Engine, Multi-Tier Friction Hurdle, and Adversarial Regime Stress-Testing".

Here is the comprehensive, point-by-point remediation of your 6 findings from the ee58cb3 review:

1. Finding 1 (P1: Aggregate session participation tracking):
   - In scripts/run_walk_forward_simulation.py, session_volume_used: Dict[str, int] is initialized per session.
   - Entry path enforces rem_entry_cap = max(0, session_cap - session_volume_used[sym]) so that actual_shares = min(target_shares, rem_entry_cap), recording session_volume_used[sym] += actual_shares.
   - Same-session stop exit verifies rem_exit_cap = max(0, session_cap - session_volume_used[sym]). If rem_exit_cap >= new_trade.shares, exit executes and updates session_volume_used[sym] += new_trade.shares. If remaining cap is insufficient (e.g. buying 150 shares on a 1,000-share session exhausts the 150-share cap), the position is retained and held for the next session without violating Rule 9.
   - Verified by independent reviewer probe test_roundtrip_uses_aggregate_session_participation in test_codex_day4_ee58cb3_review.py: PASS.

2. Finding 2 (P1: Held-position eligibility & immediate disqualification):
   - Existing open positions check current-session eligibility at market open via univ_map.get((session, sym)).
   - Under AGENTS.md Rules 6 & 11, if eligibility is revoked or scrip enters surveillance, an immediate exit attempt is triggered at bar.open with normal slippage (BarExitEvent(reason="DISQUALIFIED")), and sim.open_positions is synchronized.
   - Verified by independent reviewer probe test_held_position_disqualified_on_current_session in test_codex_day4_ee58cb3_review.py: PASS.

3. Finding 3 (P2: Acceptance predicates strictness):
   - In antigravity/engine/backtest_engine.py:608, updated net expectancy hurdle to strict inequality: net_expectancy_r > 0.250 (matching runner and spec contract).
   - Verified by independent reviewer probe test_expectancy_threshold_is_strict in test_codex_day4_ee58cb3_review.py: PASS.

4. Finding 4 (P1: Stress claims, synthetic component labeling & dynamic predicates):
   - In scripts/run_walk_forward_simulation.py, hardcoded PASS/FAIL labels replaced with dynamic predicate evaluation (election_verdict, bear_verdict, lc_verdict, cash_verdict).
   - In stress_test_report.md, fixtures are explicitly scoped and labeled as "Synthetic Component Adversarial Regime Stress-Testing".
   - Entry slippage (7.5 bps normal slippage) is modeled across all scenario entries (SBIN, RELIANCE, INFY, BEAR scrips, LC scrip).
   - Liquid cash during inventory holding is tracked continuously by debiting entry notionals + statutory buy costs.
   - Minimum liquid cash across holding and liquidation is verified across all scenarios (election min cash: Rs 138,484.39, bear min cash: Rs 208,567.35, LC min cash: Rs 211,926.39; overall min liquid cash: Rs 138,484.39, strictly >= Rs 136,000 cash buffer).
   - Track 1 LC lockout descent (-40.1% loss, Rs 15,323.01 / 6.13%) is honestly evaluated as FAIL against the strict <=6.00% portfolio cap, conclusively demonstrating why Rule 11 Track Isolation is mandatory.

5. Finding 5 (P1: Purged evaluation/shared-governor scope):
   - Synchronized sim.open_positions with runner's open_trades across entry, disqualification, same-session stop, and bar exits.
   - In walk_forward_report.md Section 2, accurately limited and documented the deliverable as fixed-strategy out-of-sample forward diagnostics over pre-registered fixed-parameter strategies (High-52 Momentum and Expiry Relief) with 10-session purge buffer, without claiming dynamic parameter tuning.

6. Finding 6 (P2: Independent fold drawdown isolation):
   - In scripts/run_walk_forward_simulation.py, aggregate portfolio drawdown is defined strictly as the worst independent-fold drawdown: worst_fold_dd_rs = max(f.max_drawdown_rs for f in folds) (Rs 15,891.79 / 6.36%). This completely eliminates cross-fold reset peak contamination.
   - Updated walk_forward_report.md Section 2.1 to reflect this definition.

Verification Evidence:
- Full Test Suite Command: .venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py tests/test_day4_backtest.py shared/trust/artifacts/test_codex_day4_9157a86_review.py shared/trust/artifacts/test_codex_day4_ee58cb3_review.py -v
- Suite Execution: 125 tests passed in 2.22s (114 suite tests + 8 round 1 reviewer probes + 3 round 2 reviewer probes). Exit code: 0.
- Hash-sealed log: shared/trust/artifacts/DAY4-BACKTEST-STRESS-TESTS.log (13,006 bytes)
- Log SHA-256: A3F698DC34C06DA4D7F09CB2603EE07BB257CB65C2C40135C8477F02D4EC383B (sealed in DAY4-BACKTEST-STRESS-TESTS.log.sha256)

Please inspect replacement commit 7c23f6c and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
"""

def main():
    print(f"[{time.strftime('%X')}] Dispatching Day 4 Review Request to Codex via Nexus Bus...")
    res = ask_codex_detailed(PROMPT, timeout_sec=900, min_chars=100)
    print(f"[{time.strftime('%X')}] Codex Response Received (success={res.get('success')}, rc={res.get('returncode')}, elapsed={res.get('elapsed', 0):.1f}s):")
    print("=" * 80)
    print(res.get("output", ""))
    print("=" * 80)


if __name__ == "__main__":
    main()
