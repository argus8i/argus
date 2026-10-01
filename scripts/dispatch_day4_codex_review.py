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

Exact Commit to Review: ee58cb3 (HEAD on branch feature/day4-backtest-and-stress-testing)
Base Branch Commit: 565d5a8 (main tip with Days 1-3 approved & merged)
Prior Review Reference: shared/trust/codex_day4_9157a86_independent_review_2026_10_02.md (CHANGES_REQUIRED on 9157a86)

Mandate:
Perform formal Day 4 peer review and acceptance gate evaluation on replacement commit ee58cb3 for Sprint Day 4: "Purged Walk-Forward Backtesting Engine, Multi-Tier Friction Hurdle, and Adversarial Regime Stress-Testing".

Here is the comprehensive, point-by-point remediation of your 9 blocking findings:

1. Finding 1 (False acceptance labels & Cash buffer reporting):
   - In scripts/run_walk_forward_simulation.py, hurdle verdicts are generated dynamically from computed predicates (pf_pass, wr_pass, exp_pass, dd_pass, cash_pass).
   - In walk_forward_report.md, metrics failing qualification criteria are honestly labeled FAIL (Profit Factor: 1.00 -> FAIL; Expectancy: -0.005R -> FAIL; Max Drawdown: 7.14% -> FAIL; Overall Gate: FAIL). Zero hardcoded PASS verdicts exist.
   - Cash buffer reporting uses true minimum liquid cash across all observations (Rs 138,698.29), correctly verifying the Rs 136,000 reserve was inviolate.
   - Explicit Section 1.1 documents why failing hurdles strictly refuse real capital deployment under Rule 1, demonstrating the protective necessity of the 60-session paper gate.

2. Finding 2 (Invented locked exits & Uncapped sales):
   - Runner bars TIME_STOP liquidation on zero-volume / locked bars (requires bar.volume > 0 and not is_locked).
   - Claude Rule 9 15% volume participation cap is strictly enforced on exits (max_sell_shares = int(0.15 * bar.volume) < trade.shares -> preserves holding).
   - evaluate_bar_exit increments holding_sessions and locked_sessions exactly once per session, avoiding double counting.
   - Thin-exit probe test_exit_respects_volume_cap passes.

3. Finding 3 (Invalid entries & Ignored entry-day losses):
   - Added breakout trigger check: bar.high >= sig.reference_price is strictly required for buy stop orders.
   - Same-session stop loss breach check: if bar.low <= new_trade.stop_loss on the entry session itself, trade executes STOP_LOSS on that entry session.
   - Probes test_buy_stop_requires_trigger and test_entry_session_stop_is_not_ignored pass.

4. Finding 4 (Current eligibility bypass):
   - Runner revalidates current execution session eligibility from authoritative universe_map.get((session, sym)) fail-closed before opening trade.
   - Probe test_entry_revalidates_current_eligibility passes.

5. Finding 5 (Sealed holdout guard disconnected):
   - run_fold_simulation enforces the sealed holdout guard directly on dates touching 2025-2026, raising PermissionError fail-closed unless policy.allow_holdout=True.
   - Probe test_actual_runner_blocks_holdout passes.

6. Finding 6 (Drawdown acceptance incomplete):
   - compute_backtest_metrics seeds peak from corpus_rs: peak = max(corpus_rs, equity_curve[0].equity), ensuring initial drops from Rs 250,000 are captured.
   - hurdle_passed includes strict predicate: max_dd_pct <= 6.0.
   - Probes test_drawdown_includes_initial_corpus and test_drawdown_gate_fails_above_six_percent pass.

7. Finding 7 (Pooled equity continuous portfolio & UNRESOLVED trade metadata):
   - UNRESOLVED trades at fold boundaries record explicit marked-to-market values (mtm_value), unrealized PnL (unrealized_pnl), and as_of_session (2024-09-30) in trades.csv, with empty exit prices/sessions.
   - daily_equity.csv is explicitly partitioned by fold_id (FOLD_1_2023, FOLD_2_2024).
   - walk_forward_report.md Section 2.1 documents that folds are independent out-of-sample slices with explicit boundary marks, avoiding false continuous portfolio claims.

8. Finding 8 (Friction repricing from unadjusted fill ledger):
   - BacktestTrade records raw_entry_price and raw_exit_price.
   - compute_ledger_net_pnl freshly slips execution prices from raw levels using policy-specific normal and gap slippage rates.
   - Flat Rs 15.93 DP charge is grouped strictly once per symbol per sell session.

9. Finding 9 (Stress acceptance overstated & synthetic isolation):
   - Stress report is generated dynamically from run_adversarial_stress_scenarios().
   - Election Volatility Shock (04-Jun-2024) models 3 slots with full itemized statutory fees + grouped DP: Rs 4,591.53 loss (1.84%) -> PASS (<= 6.00%).
   - Bear Market Grind (2022) models 8 consecutive 1R stops with itemized statutory costs: Rs 12,819.32 loss (5.13%) -> PASS (<= 6.00%), minimum liquid cash Rs 237,180.68.
   - 10-Day Lower Circuit Lockout Descent (-40.1% CROPSTER calibration on Rs 38,000 slot) yields Rs 15,249.40 loss (6.10%), honestly reported as FAIL against the strict <=6.00% portfolio cap. Report explicitly identifies this as Track 1 calibration, proving why Rule 11 Track Isolation is mandatory.
   - Cash buffer inviolability reports true liquid cash reserve rather than equity marks.

Verification Evidence:
- Suite command: .venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py tests/test_day4_backtest.py shared/trust/artifacts/test_codex_day4_9157a86_review.py -v
- Suite execution: 122 tests passed in 2.38s (114 suite tests + 8 Codex reviewer probes). Exit code: 0.
- Hash-sealed log: shared/trust/artifacts/DAY4-BACKTEST-STRESS-TESTS.log (12,591 bytes)
- Log SHA-256: F0554AFF1E8DD6D09D7A43CF9F0FB6C2F6750BB84780AC770221FAB3CC9571E1 (sealed in DAY4-BACKTEST-STRESS-TESTS.log.sha256)

Please inspect commit ee58cb3 and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
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
