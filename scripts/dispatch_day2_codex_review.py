"""
scripts/dispatch_day2_codex_review.py
=====================================
Dispatches Sprint Day 2 review request to OpenAI Codex over the Nexus Bus.
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

Active Branch: feature/day2-execution-and-risk-governor

Mandate:
Perform formal peer review on Sprint Day 2 deliverables under the Master 5-Day Sprint (ARGUS 8i Track 2 Liquid Desk):

1. Multi-Day Swing Execution Reality Engine (`antigravity/engine/execution_simulator.py`):
   - Itemized Statutory Friction (Zerodha Cash Delivery):
     * Brokerage: Rs 0.00
     * STT: 0.1% on delivery buy and sell turnover
     * Exchange Charges: NSE 0.00297%
     * SEBI Turnover Fees: 0.0001% (Rs 10 / Crore)
     * Stamp Duty: 0.015% on buy turnover only (Rs 0 on sell)
     * Depository Participant (DP) Charges: flat Rs 15.93 on delivery scrip sale (Codex Mandate 2)
     * Goods & Services Tax (GST): 18% on (Brokerage + Exchange Charges + SEBI Fees)
   - Slippage Modeling:
     * Base slippage: 7.5 bps per side on liquid F&O underlyings
     * Gap stress slippage: 25.0 bps on gap-openings
     * Adverse direction: Buy executed above benchmark; Sell executed below benchmark
   - Discrete Execution States & Circuit Mechanics (AGENTS.md Rules 3, 4, 5):
     * LOCKED_NO_OFFER: Upper Circuit lock on Buy -> fill probability 0%, 0 shares filled (Rule 3)
     * LOCKED_NO_BID: Lower Circuit lock on Exit -> fill probability 0%, position carried forward (Rules 4 & 5)
     * Gap-up open on entry -> fills at Open price + slippage
     * Gap-down open past stop -> fills at Open price - slippage, actual loss exceeds 1R planned budget
     * Claude Rule 9: 15% volume participation cap -> excess quantity results in PARTIAL fill
     * Rule 2: Absolute Rs 10.00 price floor -> sub-Rs 10 securities disqualified immediately

2. Central Risk Governor & Portfolio Accounting (`antigravity/engine/risk_governor.py`):
   - Pinned Adjusted A1 Capacity Limits (Yashu Mandate):
     * Total Corpus: Rs 2,50,000.00
     * Unencumbered Cash Buffer: Rs 1,36,000.00
     * Deployable Capital / Exposure Ceiling: Rs 1,14,000.00
     * Max Concurrent Position Slots: 3 slots (MAX_SLOTS = 3)
     * Max Single Position Slot Cap: Rs 38,000.00 (SLOT_CAP_RS = 38,000.00)
     * Planned Risk Budget per Trade: Rs 1,500.00 (1R)
     * Max Aggregate Open Risk Cap: Rs 4,500.00 (3 * Rs 1,500.00)
   - Sizing Mechanics (`compute_position_size`):
     * Exact mathematical floor without float-boundary rounding up
     * Fail-closed missing ATR (= 0 shares)
   - Sector Concentration:
     * Max 2 positions per sector
     * Unmapped sector strictly rejected fail-closed
   - Deterministic Simultaneous Signal Priority (`rank_and_allocate_signals`):
     * Deterministic ranking by priority score descending with symbol tie-breaker
   - Portfolio State Machine & Realism Accounting:
     * Circuit-locked exit lockout preserves position and slot in portfolio ledger
     * Gap-down exit losses (> 1R) reconciled truthfully into cash and equity without state corruption

3. Verification Evidence:
   - Reproduction artifact recorded at `shared/trust/artifacts/DAY2-EXECUTION-RISK-GOVERNOR-TESTS.log`:
     * 26 adversarial tests in `tests/test_execution_risk_governor.py` passing 100%
     * 12 contract tests in `tests/test_day1_data_contracts.py` passing 100%
     * Total: 38 passed in 0.19s, exit code: 0

Please review the implementation and test logs, verify that all requirements and invariants hold, and provide your formal independent review verdict (APPROVED or CHANGES_REQUIRED) with specific findings.
"""

def main():
    print("Dispatching Sprint Day 2 review request to OpenAI Codex (timeout=300s, chat_only=True)...")
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
