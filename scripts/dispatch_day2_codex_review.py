"""
scripts/dispatch_day2_codex_review.py
=====================================
Dispatches Sprint Day 2 re-review request to OpenAI Codex over the Nexus Bus.
"""
import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from antigravity.daemons.tri_agent_bus import ask_codex_detailed

PROMPT = """Signed Nexus review request for OpenAI Codex (Senior Systems, Execution-Reality & Reliability Engineer).

Author: Antigravity (Quantitative Modeling & Infrastructure Orchestrator)
Scope:
- antigravity/engine/execution_simulator.py
- antigravity/engine/risk_governor.py
- tests/test_execution_risk_governor.py
- scripts/run_and_record_day2_suite.py
- shared/trust/artifacts/DAY2-EXECUTION-RISK-GOVERNOR-TESTS.log
- shared/trust/artifacts/DAY2-EXECUTION-RISK-GOVERNOR-TESTS.log.sha256

Exact Commit to Review: 2a0a240 (on branch feature/day2-execution-and-risk-governor)
Parent Commit: 4a1663e

Mandate:
Perform formal re-review and acceptance gate evaluation on commit 2a0a240 resolving all findings from your Round 2 review:

1. Remediation of Finding 4 (Cash Buffer Enforced at Actual Fill):
   - In `confirm_fill_from_reservation`: Added strict check that actual fill outlay (actual_notional + transaction_costs) leaves remaining cash >= Rs 136,000 cash buffer. If below, raises `ValueError("CASH_BUFFER_BREACH_AT_FILL")` fail-closed.
   - Tested in `test_codex_round2_cash_buffer_at_fill`.

2. Remediation of Finding 3 (Partial Fills Preserve Outstanding Reservation):
   - In `confirm_fill_from_reservation`: Confirming partial quantity (e.g. 15 shares of 100) preserves the remaining 85 shares in `self.pending_reservations` with updated notional and risk. Reservation popped only when fully filled.
   - Tested in `test_codex_round2_partial_fill_preserves_reservation`.

3. Remediation of Finding 3 (NaN, Inf, and Non-Positive Input Validation):
   - In `confirm_fill_from_reservation`: Rejects `float('nan')`, inf, boolean, negative prices, and zero/negative filled quantities fail-closed with `ValueError("FAIL-CLOSED")`.
   - Tested in `test_codex_round2_actual_fill_nan_rejected`.

4. Remediation of Finding 2 (Execution-Event Identity & Idempotency):
   - In `reconcile_exit`: Accepts `exit_event_id` and records processed event IDs in `self.processed_exit_events`. Replaying an exit event ID raises `ValueError("DUPLICATE_EXIT_EVENT")` fail-closed.
   - Tested in `test_codex_round2_exit_event_idempotency`.

5. Remediation of Finding 1 (Non-Stop / Take-Profit Touch Verification & Benchmark):
   - In `simulate_exit`: Accepts `target_price: Optional[float] = None`. For `TAKE_PROFIT` / `TARGET`, verifies `bar.high >= target_price`. If untouched, returns `QUEUED` with 0 filled shares and `position_remains_open=True`.
   - Benchmarks execution against `target_price` (or gap up open) minus adverse slippage, NOT against stop price. Realized PnL and R-multiple are positive for target exits.
   - Tested in `test_codex_round2_take_profit_touch_and_benchmark`.

6. Remediation of Finding 8 (Cost Separation & Entry Basis Persistence):
   - In `ExecutionReport`: Separated `exit_transaction_costs` from combined `total_cost`.
   - In `reconcile_exit`: Deducts ONLY exit transaction friction from sale proceeds (accepting `exit_transaction_costs`), preventing double-deduction of entry costs from cash.
   - On partial exits, pro-rates remaining `entry_costs` basis on the remaining shares.
   - Tested in `test_codex_round2_exit_reconciliation_no_double_entry_deduction`.

Empirical Evidence:
- Complete test suite: 40 adversarial tests in `tests/test_execution_risk_governor.py` + 12 in `tests/test_day1_data_contracts.py` (total 52 passed in 0.21s, exit code 0).
- Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py -v`
- Execution runner & recorder: `scripts/run_and_record_day2_suite.py`
- Hash-sealed log: `shared/trust/artifacts/DAY2-EXECUTION-RISK-GOVERNOR-TESTS.log`
- Log SHA-256: `9B2E0D7F33C044738F5B315B7D18886D9D285D5EA5C49B44D5285ED3F93774F7` (sealed in `DAY2-EXECUTION-RISK-GOVERNOR-TESTS.log.sha256`)

Please inspect commit 2a0a240 and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
"""

def main():
    print("Dispatching Sprint Day 2 re-review request to OpenAI Codex (timeout=300s, chat_only=True)...")
    t0 = time.time()
    res = ask_codex_detailed(PROMPT, timeout_sec=300, chat_only=True)
    elapsed = time.time() - t0
    print(f"Elapsed: {elapsed:.2f}s")
    print(f"Success: {res.get('success')}")
    print(f"Return code: {res.get('returncode')}")
    print(f"Error: {res.get('error')}")
    print("\n--- CODEX OUTPUT ---")
    print(res.get("output", ""))
    print("--- END OUTPUT ---\n")

if __name__ == "__main__":
    main()
