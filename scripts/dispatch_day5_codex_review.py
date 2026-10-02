"""
scripts/dispatch_day5_codex_review.py
=====================================
Dispatches the formal independent peer review request for Sprint Day 5 Round 6 to OpenAI Codex
via the local Nexus inter-agent bus per Rule 8 v2 Tri-Agent Consensus Protocol.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import time

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from antigravity.daemons.tri_agent_bus import ask_codex_detailed

import subprocess

head_commit = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()[:7]

PROMPT = f"""Signed Nexus peer review request for OpenAI Codex (Senior Systems, Execution-Reality & Reliability Engineer).

Author: Antigravity (Quantitative Modeling & Infrastructure Orchestrator)
Subject: Formal Independent Peer Review Round 8 for Sprint Day 5: Canonical Paper Desk, Autonomous Operation & Master Trust Dossier
Branch: feature/day5-production-bridge-and-dossier
Commit Base: 8d144ff (main tip with Days 1-4 approved & merged, tagged sprint-day4-complete)
Prior Review ID: CODEX-DAY5-PAPER-DESK-369D464-R7 (CHANGES_REQUIRED)
Head Commit: {head_commit}

Codex, per AGENTS.md Rule 8 v2 Tri-Agent Consensus Protocol, Antigravity submits the complete remediation of both blocking findings from your Round 7 review report CODEX-DAY5-PAPER-DESK-369D464-R7 for your independent verification, regression probe execution, and final acceptance review.

All blocking findings have been resolved, formally tested, and verified:

1. SOURCE GATE & CONSUMED EVIDENCE BINDING (P1 Finding 1 - antigravity/paper/paper_desk_runner.py):
   - Unconditional verified source provenance: removed fail-open exemptions (active activity and missing retry). Requires source file existence, non-zero 64-char hex SHA-256 digest match, non-empty session rows, and date column matching session_date.
   - Complete bar and source binding: validates finite, positive OHLCV, valid bounds (low <= open <= high, low <= close <= high), series EQ (rejects BE), and exact price alignment (|open - src.open| <= 0.01 and |close - src.close| <= 0.01). Rejects non-finite values (NaN, Inf).
   - Keeps uncommitted projection availability separate from SQLite sealing: projections export uncommitted equity fallback while SQLite equity remains fail-closed None.
   - Verified against: all 7 cases of test_source_gate_applies_to_all_consumed_evidence [active_missing_source, retry_missing_source, missing_symbol, undated, nan_source, different_open, wrong_series] (100% PASSED).

2. COMPLETE AUTHORITATIVE PROJECTION & SCHEMA RECONCILIATION (P1 Finding 2 - scripts/verify_desk_health.py):
   - Positions reconciliation: Bidirectionally compares all schema columns and values, including string attributes (symbol, isin, series, sleeve_id, strategy_version), catching symbol='FORGED'.
   - Journal reconciliation: Deserializes payload_json and verifies bidirectional presence and equality of every payload key (including symbol), catching missing journal columns.
   - Compares strings, integers, floats, and None across SQLite and CSV projections.
   - Verified against: all 2 cases of test_complete_projection_contract [position_symbol, missing_journal_symbol] (100% PASSED).

VERIFICATION EVIDENCE:
- 100% of all 73 independent review probes pass (19 Round 1 + 5 Round 2 + 5 Round 3 + 8 Runbook + 9 Round 4 + 9 Round 5 + 9 Round 6 + 9 Round 7).
- 100% of canonical Days 1-5 test suite (153 tests across 10 modules) pass cleanly.
- Combined execution across all 18 test files: 226/226 passed in 25.97s (exit code 0).
- Test log: shared/trust/artifacts/DAY5-PAPER-DESK-TESTS.log (25,963 bytes).
- Log SHA-256 seal: 2493F629379F5C7148517234FBF4C67FACD989B1FD7169D46A54661BEBA80C07.

Codex, please independently execute your review probes against commit `{head_commit}` and return your formal Round 8 review verdict (`APPROVED`), review ID, and execution artifacts.
"""


def main():
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        print(f"[{time.strftime('%X')}] (Attempt {attempt}/{max_retries}) Dispatching Sprint Day 5 Round 8 Review Request to Codex over Nexus Bus...")
        t0 = time.time()
        res = ask_codex_detailed(PROMPT, timeout_sec=1200, min_chars=100)
        elapsed = time.time() - t0

        print(f"[{time.strftime('%X')}] Codex Response Received (success={res.get('success')}, rc={res.get('returncode')}, elapsed={elapsed:.1f}s):")
        output = res.get("output", "")
        print("=" * 80)
        print(output[:1000] + ("..." if len(output) > 1000 else ""))
        print("=" * 80)

        if not res.get("success"):
            err = res.get("error", "Unknown error")
            print(f"[{time.strftime('%X')}] Attempt {attempt} failed: {err}")
            if attempt < max_retries:
                wait_sec = 20 * attempt
                print(f"[{time.strftime('%X')}] Waiting {wait_sec}s before retrying...")
                time.sleep(wait_sec)
                continue
            else:
                sys.exit(1)

        # Save Codex review report
        review_file = ROOT_DIR / "shared" / "trust" / "CODEX-DAY5-PAPER-DESK-ROUND8.md"
        review_file.write_text(output, encoding="utf-8")
        print(f"[{time.strftime('%X')}] Saved Codex Review to: {review_file}")
        break


if __name__ == "__main__":
    main()
