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

Exact Commit to Review: 95390f4 (on branch feature/day2-execution-and-risk-governor)
Parent Commit: 2bc6503

Mandate:
Perform formal re-review and acceptance gate evaluation on commit 95390f4 resolving the boundary rounding edge-case in Finding 1 from your prior review:

1. Remediation of Finding 1 Boundary Rounding Edge-Case:
   - In `confirm_fill`: Replaced 4-decimal rounding (`round(..., 4)`) with full-precision floating-point weighted entry price `weighted_entry_price = new_notional / new_shares`.
   - In both `confirm_fill` and `confirm_fill_from_reservation`: Open risk is calculated consistently and identically from cumulative notional and stop basis:
     `stop_basis = round(new_shares * stop_price, 2)`
     `open_risk_rs = round(new_notional - stop_basis, 2)`
   - In `reconcile_exit`: Residual open risk on partial exits is likewise calculated consistently from remaining notional and remaining stop basis.
   - Tested in `test_codex_round4_partial_fill_precision_and_post_fill_risk_cap` directly using your exact test case (`stop = 10.000051 - 1500 / 3799`, fills of 1 share @ 10.0 and 3798 shares @ 10.000051). The recorded ledger now strictly enforces `open_risk_rs == 1500.00 <= 1500.00` without any rounding distortion.

2. Prior Findings Status:
   - Findings 2–4 accepted in prior round (atomic mandatory exit_event_id, prorated entry costs on partial exits, finite positive target price validation).

Empirical Evidence:
- Complete test suite: 45 adversarial tests in `tests/test_execution_risk_governor.py` + 12 in `tests/test_day1_data_contracts.py` (total 57 passed in 0.16s, exit code 0).
- Reproduction command: `.venv\\Scripts\\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py -v`
- Execution runner & recorder: `scripts/run_and_record_day2_suite.py`
- Hash-sealed log: `shared/trust/artifacts/DAY2-EXECUTION-RISK-GOVERNOR-TESTS.log`
- Log SHA-256: `CB3CDCC554E497D67DD8A83E0C7F440092B78B3FDA9397F3A8008F2582F84A0A` (normalized LF, sealed in `DAY2-EXECUTION-RISK-GOVERNOR-TESTS.log.sha256`)

Please inspect commit 95390f4 and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED).
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
