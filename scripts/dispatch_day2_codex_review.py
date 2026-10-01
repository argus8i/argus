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

Exact Commit to Review: be0e591 (on branch feature/day2-execution-and-risk-governor)
Parent Commit: f226ef7

Mandate:
Perform formal re-review and acceptance gate evaluation on commit be0e591 resolving all 4 findings from your prior review:

1. Remediation of Finding 1 (Cumulative Risk & Weighted Entry Basis on Partial Fills):
   - In `confirm_fill`: When adding shares to an existing active position, recalculates weighted-average entry price `new_notional / new_shares`, updates notional, re-evaluates open risk against stop price, and aggregates transaction costs into `pos["entry_costs"]`.
   - In `confirm_fill_from_reservation`: Evaluates CUMULATIVE position notional (`comb_notional`) and CUMULATIVE position risk (`comb_risk`) combining existing shares with the proposed fill. If `comb_notional > slot_cap_rs` (Rs 38,000) or `comb_risk > risk_per_trade_rs` (Rs 1,500), rejects fail-closed with `ValueError("EXPOSURE_OR_RISK_BREACH")`.
   - Tested in `test_codex_round3_partial_fills_cumulative_risk_and_entry_basis`.

2. Remediation of Finding 2 (Mandatory & Atomic Exit Idempotency):
   - In `reconcile_exit`: `exit_event_id` is strictly MANDATORY (rejects None, empty, or whitespace with `ValueError("FAIL-CLOSED: exit_event_id is required")`).
   - Validates existence of active position, valid shares quantity, finite positive exit price, and non-negative exit transaction costs BEFORE recording `evt_id` in `self.processed_exit_events`.
   - If validation fails, `evt_id` is NOT consumed, allowing corrected retry with the same ID.
   - Replaying the same `exit_event_id` after successful reconciliation raises `ValueError("DUPLICATE_EXIT_EVENT")` fail-closed.
   - Tested in `test_codex_round3_exit_idempotency_mandatory_and_atomic`.

3. Remediation of Finding 3 (Prorated Entry Costs Basis on Partial Exits):
   - In `reconcile_exit`: On partial exits (`remaining_shares > 0`), prorates `pos["entry_costs"]` based on remaining share ratio: `pos["entry_costs"] -= round(pos["entry_costs"] * (shares_to_sell / open_shares), 2)`.
   - Residual entry cost basis is accurately preserved for subsequent partial/final exits.
   - Tested in `test_codex_round3_partial_exit_prorates_entry_costs`.

4. Remediation of Finding 4 (Finite Positive Target Validation):
   - In `simulate_exit`: For `trigger_reason in ("TAKE_PROFIT", "TARGET")`, strictly validates that `target_price` is a finite positive number (`math.isfinite(x) and x > 0 and not isinstance(x, bool)`). Rejects missing, negative, zero, NaN, inf, or boolean targets fail-closed with `ValueError("FAIL-CLOSED")`.
   - Tested in `test_codex_round3_target_validation_finite_positive`.

5. Newline & Cryptographic Hash Normalization:
   - `scripts/run_and_record_day2_suite.py` explicitly enforces standard LF (`\n`) newlines across platforms (`newline="\\n"`).
   - Log SHA-256 is computed directly on the normalized LF bytes matching the Git blob object.

Empirical Evidence:
- Complete test suite: 44 adversarial tests in `tests/test_execution_risk_governor.py` + 12 in `tests/test_day1_data_contracts.py` (total 56 passed in 0.30s, exit code 0).
- Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py -v`
- Execution runner & recorder: `scripts/run_and_record_day2_suite.py`
- Hash-sealed log: `shared/trust/artifacts/DAY2-EXECUTION-RISK-GOVERNOR-TESTS.log`
- Log SHA-256: `7356435880E32DBAC964B0CFF03C34ECAA136FE5BEF6A44A560F79CC75C9E17D` (normalized LF, sealed in `DAY2-EXECUTION-RISK-GOVERNOR-TESTS.log.sha256`)

Please inspect commit be0e591 and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
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
