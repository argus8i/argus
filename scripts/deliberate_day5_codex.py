"""
scripts/deliberate_day5_codex.py
================================
Proactive Tri-Agent Bus Deliberation for Sprint Day 5 Architecture & Build Plan.
Per Rule 8 v2 Invariant 5 (Mandatory Bus Deliberation Invariant, Yashu 1 Oct 2026).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from antigravity.daemons.tri_agent_bus import ask_codex_detailed

PROMPT = """Signed Nexus deliberation request for OpenAI Codex (Senior Systems, Execution-Reality & Reliability Engineer).

Author: Antigravity (Quantitative Modeling & Infrastructure Orchestrator)
Subject: Sprint Day 5 Build Plan & Architecture Deliberation (Rule 8 v2 Invariant 5)
Branch: feature/day5-production-bridge-and-dossier
Commit Base: 8d144ff (main tip with Days 1-4 approved & merged, tagged sprint-day4-complete)

Codex, per Rule 8 v2 Invariant 5 (Mandatory Bus Deliberation Invariant), Antigravity is proactively initiating cross-agent deliberation on the proposed architecture, data contracts, and verification methodology for Sprint Day 5: "Canonical Paper Desk, Autonomous Operation & Master Trust Dossier".

Sprint Day 4 has been formally approved by Codex (commit 14a3783, verified by 137 passing tests and 4 footer probes), recorded in shared/trust/reviews.jsonl, and merged to main.

Here is the proposed design for Sprint Day 5:

1. ARCHITECTURAL SCOPE & MODULE DESIGN:
   - Module: `antigravity/paper/paper_desk_runner.py`
   - Test Suite: `tests/test_day5_paper_desk.py`
   - Complete production bridge uniting Days 1-4:
     * Day 1: Daily universe, Bhavcopy + MTO ingestion, and fail-closed surveillance/F&O filters (`PreOpenEligibilityValidator`).
     * Day 2: `PortfolioRiskGovernor` with Adjusted A1 3-slot cap, Rs 38,000 slot cap, Rs 114,000 exposure ceiling, Rs 1,500 trade risk, and Rs 136,000 unencumbered cash buffer; `ExecutionSimulator` with discrete fill states and 15% volume participation cap.
     * Day 3: Quantitative Alpha Strategy Engine running Sleeves A (`DeliveryAccumulationStrategy`), B (`High52MomentumStrategy`), and C (`ExpiryReliefStrategy`) with deterministic tie-breaking.
     * Day 4: Realistic friction accounting (statutory costs, DP charges grouped per symbol/sell session, conservative bar resolution, locked bar intent persistence, and recovery open liquidation).

2. CANONICAL PAPER TRADING DESK SPECIFICATION:
   - Journal Storage:
     * Orders & Fills Ledger: `shared/track2_liquid/paper/canonical_paper_journal.csv`
     * Open Position Ledger: `shared/track2_liquid/paper/open_positions.csv`
     * Daily Portfolio Equity Ledger: `shared/track2_liquid/paper/daily_portfolio_equity.csv`
   - Operating Invariants:
     * Real capital deployment strictly disabled (`allow_live_broker = False`, Rule 1 observation gate).
     * Exploratory/diagnostic paper status clearly watermarked until 60 prospective sessions and 20 fillable entries pass with positive net expectancy per Rule 1.
     * Open inventory carried forward session-to-session; unresolved trades marked-to-market daily.
     * Daily pre-open eligibility checks: if scrip enters ASM/GSM or exits F&O, immediate exit attempt queued at next session open with normal slippage per Rules 6 & 11.
     * Volume participation cap strictly enforced at <=15% of actual daily session volume (Rule 9).

3. SCHEDULED TIMING & ORCHESTRATION:
   - 08:45 IST (Pre-Open): Reads verified previous-day EOD data, checks current surveillance lists, generates next-session candidate orders.
   - 15:45 IST (Post-Close): Ingests new Bhavcopy + MTO, executes fill simulation and position exit checks, reconciles cash and portfolio equity, appends to paper journal.

4. MASTER SYSTEM ARCHITECTURE SPECIFICATION & RUNBOOK ("Putting a Full Stop"):
   - `shared/docs/ARGUS_SYSTEM_ARCHITECTURE_SPECIFICATION.md`:
     * Complete system architecture diagram, module contracts, and data flows.
     * Formal mathematical definitions of all 4 alpha sleeves (including Sleeve D PEAD holdout).
     * Risk governor mathematical proofs and divisor formulas.
     * Full regression test matrix and reproduction artifacts across Days 1-5.
   - `shared/docs/YASHU_OPERATOR_RUNBOOK.md`:
     * Plain-language operator manual for Yashu.
     * 2-minute daily routine: how to inspect pre-open signals, verify EOD paper journals, monitor cash buffer, and verify system health.

5. QUESTIONS FOR CODEX DELIBERATION:
   a) What specific data-contract columns and schemas do you require for `canonical_paper_journal.csv` and `daily_portfolio_equity.csv` to ensure seamless downstream auditing?
   b) Are there specific edge cases in paper desk position persistence (e.g. corporate actions, weekend gap opens, unexpected exchange holidays) that you want codified in `tests/test_day5_paper_desk.py`?
   c) What specific acceptance tests or fail-closed invariants would you like to see formalized as failing-first regression tests for Day 5?

Please provide your technical feedback, architectural recommendations, and required acceptance constraints for Sprint Day 5.
"""

def main():
    print(f"[{time.strftime('%X')}] Dispatching Day 5 Architecture Deliberation to Codex via Nexus Bus...")
    res = ask_codex_detailed(PROMPT, timeout_sec=900, min_chars=100)
    print(f"[{time.strftime('%X')}] Codex Response Received (success={res.get('success')}, rc={res.get('returncode')}, elapsed={res.get('elapsed', 0):.1f}s):")
    print("=" * 80)
    print(res.get("output", ""))
    print("=" * 80)


if __name__ == "__main__":
    main()
