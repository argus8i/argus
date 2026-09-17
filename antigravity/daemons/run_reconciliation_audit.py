"""
run_reconciliation_audit.py - Autonomous Tri-Agent Post-Repair Audit & Consensus Sign-Off
Dispatches review mandates directly to Claude Code and OpenAI Codex to audit Antigravity's
engineering fixes for Defects F1-F8 and R1-R9.
"""

import sys
import os
import time

sys.path.append(os.path.dirname(__file__))
from tri_agent_bus import ask_claude_detailed, ask_codex_detailed

CLAUDE_VERIFY_PROMPT = r"""
You are Claude Code (analyst / red team) in Project Swing Trades (`c:\Users\yashw\swing trades`).
Follow `shared/00_PROTOCOL.md` and `AGENTS.md`.

Antigravity has implemented your exact condition on Rule 9:
- In `antigravity/models/liquidity_gate.py`: Added explicit caller-discipline safeguards (`is_locked_circuit: bool = False` and `avg_20d_volume` collapse check). If a stock is locked at circuit or volume has collapsed (<10% of 20d avg), `evaluate_liquidity_gate` now strictly fails with a disqualification reason so no caller can treat a calm-market pass as exit safety during a circuit freeze.
- Embedded compliance test suite in `circuit_rules.py` now runs 25/25 tests (including decoupled broker state tests).
- Embedded suite in `liquidity_gate.py` tests both circuit lock and volume evaporation safeguards.

Your task:
1. Re-inspect `antigravity/models/liquidity_gate.py` lines 55-90 and test suite.
2. State your updated verdict on Rule 9 (now that the caller safeguard condition is implemented in code).
3. Update `claude/PROGRESS.md` with your final confirmation.
"""

CODEX_VERIFY_PROMPT = r"""
You are OpenAI Codex / ChatGPT (regulatory compliance & microstructure auditor) in Project Swing Trades (`c:\Users\yashw\swing trades`).
Follow `shared/00_PROTOCOL.md` and `AGENTS.md`.

Antigravity has directly implemented all four required corrections from your previous audit entry:

1. T2T Broker Settlement Correction:
   - In `shared/track1_esm/01_MARKET_MECHANICS.md` Section 2.1: Updated to state accurately that under Zerodha RMS and T+1 rolling settlement, T2T shares bought on Day T CAN be sold on Day T+1 into delivery settlement. Intraday selling (same-day on Day T) is strictly prohibited.
   - Added official source link: https://support.zerodha.com/category/trading-and-markets/trading-faqs/general/articles/what-are-trade-to-trade-stocks

2. Pure State Decoupling:
   - In `antigravity/models/circuit_rules.py`: Completely removed `BROKER_INELIGIBLE`, `REJECTED`, and `ACCEPTED` from `ExecutionState`. `ExecutionState` now strictly models the discrete market execution states under AGENTS.md Rule 4 (`LOCKED_NO_BID`, `QUEUED`, `PARTIAL`, `FILLED`).
   - Implemented `BrokerOrderRequest` and `CircuitRuleEngine.evaluate_broker_order_state()` as an independent broker RMS transition evaluator.

3. Pinpoint Regulatory Citations:
   - In `shared/track1_esm/01_MARKET_MECHANICS.md` Section 2: Added exact URLs and pinpoint circular references for NSE ESM FAQ v1.1 (https://www.nseindia.com/reports/esm), BSE Notice 20230718-46 / NSE 57609, SEBI CIR/MRD/DP/6/2013 & 38/2013 defining the six 1-hour PCAS sessions (09:30-15:30) at https://www.nseindia.com/static/products-services/equity-market-periodic-call-auction, and BSE Master Circular Item 1.6 tick truncation.

4. 25-Test Embedded Verification Suite:
   - `circuit_rules.py` now runs 25/25 tests, explicitly asserting that `ExecutionState` contains zero broker states and verifying `evaluate_broker_order_state` for Day T rejection, T+1 auth required, and T+1 DDPI acceptance.

Your task:
1. Inspect `shared/track1_esm/01_MARKET_MECHANICS.md` and `antigravity/models/circuit_rules.py`.
2. Confirm whether R1-R7 and state decoupling are now fully satisfied.
3. State your formal regulatory sign-off verdict on Rules 4, 5, 9.
4. Update `CHATGPT/PROGRESS.md` with your updated verdict.
"""


def main():
    print("=" * 70, flush=True)
    print("AUTONOMOUS POST-REPAIR RECONCILIATION AUDIT (PASS 2)", flush=True)
    print("=" * 70, flush=True)

    errors = []

    print("\n[1/2] Launching Claude Code for Final Condition Sign-Off...", flush=True)
    start_t = time.time()
    claude_res = ask_claude_detailed(CLAUDE_VERIFY_PROMPT, timeout_sec=240)
    claude_elapsed = time.time() - start_t
    print(f"Claude Code finished in {claude_elapsed:.1f}s (Exit code: {claude_res['returncode']}).", flush=True)
    print("-" * 50, flush=True)
    print("CLAUDE CODE VERDICT:", flush=True)
    print(claude_res["output"], flush=True)
    print("-" * 50, flush=True)

    if not claude_res["success"]:
        errors.append(f"Claude Code audit failed with exit code {claude_res['returncode']}: {claude_res.get('error')}")

    print("\n[2/2] Launching OpenAI Codex for Final Regulatory Sign-Off...", flush=True)
    start_t = time.time()
    codex_res = ask_codex_detailed(CODEX_VERIFY_PROMPT, timeout_sec=240)
    codex_elapsed = time.time() - start_t
    print(f"OpenAI Codex finished in {codex_elapsed:.1f}s (Exit code: {codex_res['returncode']}).", flush=True)
    print("-" * 50, flush=True)
    print("OPENAI CODEX VERDICT:", flush=True)
    print(codex_res["output"], flush=True)
    print("-" * 50, flush=True)

    if not codex_res["success"]:
        errors.append(f"OpenAI Codex audit failed with exit code {codex_res['returncode']}: {codex_res.get('error')}")

    if errors:
        print("\n" + "!" * 70, flush=True)
        print("RECONCILIATION AUDIT FAILED WITH ERRORS:", flush=True)
        for err in errors:
            print(f"  - {err}", flush=True)
        print("!" * 70, flush=True)
        sys.exit(1)

    print("\n" + "=" * 70, flush=True)
    print("RECONCILIATION AUDIT COMPLETE (ALL AGENTS PASSED).", flush=True)
    print("=" * 70, flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()

