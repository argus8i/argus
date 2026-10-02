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
- shared/track2_liquid/backtests/trade_fills.csv
- shared/track2_liquid/backtests/daily_equity.csv
- shared/trust/artifacts/DAY4-BACKTEST-STRESS-TESTS.log
- shared/trust/artifacts/DAY4-BACKTEST-STRESS-TESTS.log.sha256
- shared/trust/artifacts/test_codex_day4_9157a86_review.py
- shared/trust/artifacts/test_codex_day4_ee58cb3_review.py
- shared/trust/artifacts/test_codex_day4_7c23f6c_review.py
- shared/trust/artifacts/test_codex_day4_90255e7_review.py
- shared/trust/artifacts/test_codex_day4_bf510da_review.py
- shared/trust/codex_day4_bf510da_independent_review_2026_10_02.md

Exact Commit to Review: 14a3783359352e151bce13808444ba7e0be65106 (HEAD on branch feature/day4-backtest-and-stress-testing)
Base Branch Commit: 565d5a8 (main tip with Days 1-3 approved & merged)
Prior Review Reference: shared/trust/codex_day4_bf510da_independent_review_2026_10_02.md (CHANGES_REQUIRED on bf510da)

Mandate:
Perform formal Day 4 peer review and acceptance gate evaluation on replacement commit 14a3783 for Sprint Day 4: "Purged Walk-Forward Backtesting Engine, Multi-Tier Friction Hurdle, and Adversarial Regime Stress-Testing".

Here is the comprehensive, point-by-point remediation of your 3 findings from the bf510da review:

1. Finding 1 (P1: Locked-session time stop persistence & recovery liquidation):
   - In antigravity/engine/backtest_engine.py:474-477 (evaluate_bar_exit), when bar.volume == 0 or circuit lockout occurs, the engine now checks if holding sessions expired (trade.holding_sessions >= 10 for HIGH52_MOMENTUM, >= 5 for EXPIRY_RELIEF) and persists trade.pending_exit_reason = "TIME_STOP".
   - In scripts/run_walk_forward_simulation.py:206-213, when trade.holding_sessions >= max_holding and bar.volume == 0 or is_locked, trade.pending_exit_reason = "TIME_STOP" is persisted.
   - On the next session upon liquid recovery, Priority 1 in runner open positions loop immediately attempts liquidation at market open at bar.open with normal slippage (raw_price = 103.0 in test fixture).
   - Verified by reviewer probes: test_locked_time_stop_persists_at_fold_boundary and test_locked_time_stop_liquidates_at_recovery_open in test_codex_day4_bf510da_review.py: PASS.

2. Finding 2 (P2: Strict finite cash and equity observation validation):
   - In antigravity/engine/backtest_engine.py:713-736 (compute_backtest_metrics), the engine now iterates through every supplied point in equity_curve and enforces that each observation contains valid finite cash and equity (math.isfinite(c) and math.isfinite(e)).
   - Any missing attribute or non-finite value (NaN, Inf) causes valid_observations = False, immediately failing closed (cash_passed = False, hurdle_passed = False).
   - Verified by reviewer probes: test_incomplete_cash_series_fails_closed[nan_cash] and test_incomplete_cash_series_fails_closed[missing_cash] in test_codex_day4_bf510da_review.py: PASS.

3. Finding 3 (P2: Verification footer derived from validated execution evidence):
   - In scripts/run_walk_forward_simulation.py, implemented derive_verification_footer() which reads and verifies shared/trust/artifacts/DAY4-BACKTEST-STRESS-TESTS.log and its SHA-256 sidecar, validates the cryptographic seal against file content, and parses the actual test execution summary and exit code.
   - Completely removed unconditional hardcoded assertions (e.g. "133 passed (Exit code: 0)"), replacing them with dynamically derived evidence and reproduction commands.
   - In shared/track2_liquid/backtests/stress_test_report.md, Section 5 now dynamically reflects the validated 137 passed tests and cryptographic SHA-256 seal.

Verification Evidence:
- Full Test Suite Command: .venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py tests/test_day4_backtest.py shared/trust/artifacts/test_codex_day4_9157a86_review.py shared/trust/artifacts/test_codex_day4_ee58cb3_review.py shared/trust/artifacts/test_codex_day4_7c23f6c_review.py shared/trust/artifacts/test_codex_day4_90255e7_review.py shared/trust/artifacts/test_codex_day4_bf510da_review.py -v
- Suite Execution: 137 tests passed in 1.81s (114 base + 8 R1 + 3 R2 + 4 R3 + 4 R4 + 4 R5). Exit code: 0.
- Hash-sealed log: shared/trust/artifacts/DAY4-BACKTEST-STRESS-TESTS.log (14,666 bytes)
- Log SHA-256: 7CD21C6C1CA02BDA38FE0AB5A4156D1B738A6F6EDA4BC6D805F05D9E4B6EF42A (sealed in DAY4-BACKTEST-STRESS-TESTS.log.sha256)
- Review Reproduction Check: .venv\\Scripts\\python.exe shared/trust/artifacts/run_codex_day4_bf510da_review.py --inspect (Exit code: 0 across suite, probes, and inspection)

Please inspect replacement commit 14a3783 and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
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
