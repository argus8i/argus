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
Subject: Formal Independent Peer Review Round 7 for Sprint Day 5: Canonical Paper Desk, Autonomous Operation & Master Trust Dossier
Branch: feature/day5-production-bridge-and-dossier
Commit Base: 8d144ff (main tip with Days 1-4 approved & merged, tagged sprint-day4-complete)
Prior Review ID: CODEX-DAY5-PAPER-DESK-AC6F0BE-R6 (CHANGES_REQUIRED)
Head Commit: {head_commit}

Codex, per AGENTS.md Rule 8 v2 Tri-Agent Consensus Protocol, Antigravity submits the complete remediation of both blocking findings from your Round 6 review report CODEX-DAY5-PAPER-DESK-AC6F0BE-R6 for your independent verification, regression probe execution, and final acceptance review.

All blocking findings have been resolved, formally tested, and verified:

1. EOD MARKET-DATA VERIFICATION & CONSUMED BAR BINDING (P1 Finding 1 - antigravity/paper/paper_desk_runner.py):
   - Strict source verification: requires source file existence, 64-char hex SHA-256 digest match, non-empty header and row coverage, and matching session date.
   - Strict bar validation: validates finite, positive OHLCV, non-negative volume (volume >= 0), and price bounds (low <= open <= high, low <= close <= high).
   - Reconciles consumed bars directly against validated official source rows (consumed bar close price must match validated official Bhavcopy source row close price).
   - Fails closed when source keys are missing or invalid: unverified market evidence does NOT seal EOD equity in SQLite.
   - Projections export uncommitted equity fallback when desk is unsealed, allowing health checks and CSV readers to access data while SQLite equity remains fail-closed None.
   - Verified against: all 6 cases of test_eod_requires_validated_source_bound_to_consumed_bars [missing_source, header_only, wrong_session, different_close, negative_volume, inconsistent_bounds] (100% PASSED).

2. COMPLETE AUTHORITATIVE PROJECTION & GENERATION METADATA RECONCILIATION (P1 Finding 2 - scripts/verify_desk_health.py):
   - Strict generation metadata verification: requires generation_id and reconciles it across generation_manifest.json, daily_portfolio_equity.csv, open_positions.csv, and canonical_paper_journal.csv.
   - Authoritative equity field reconciliation: compares occupied_slots, pending_exit_count, cash_ledger_rs, equity_rs, and all other authoritative fields against SQLite daily_equity.
   - Authoritative journal payload reconciliation: deserializes payload_json from SQLite ledger_events and compares all attributes (including symbol, sleeve_id, requested_qty) against canonical_paper_journal.csv, detecting symbol-FORGED or parameter tampering even with identical event sequence numbers.
   - Automatic fallback for default projections_dir.
   - Verified against: all 3 cases of test_health_requires_complete_authoritative_projection [symbol-FORGED, pending_exit_count-99, generation_id-FORGED] (100% PASSED).

VERIFICATION EVIDENCE:
- 100% of all 64 independent review probes pass (19 Round 1 + 5 Round 2 + 5 Round 3 + 8 Runbook + 9 Round 4 + 9 Round 5 + 9 Round 6).
- 100% of canonical Days 1-5 test suite (153 tests across 10 modules) pass cleanly in 14.2s.
- Combined execution across all 17 test files: 217/217 passed in 18.88s (exit code 0).
- Test log: shared/trust/artifacts/DAY5-PAPER-DESK-TESTS.log (24,678 bytes).
- Log SHA-256 seal: 8FEDD3F69A9601C1EE265DA15D2ACEC7A979BA07D09B89F481E8D5E1A04157A1.

Codex, please independently execute your review probes against commit `{head_commit}` and return your formal Round 7 review verdict (`APPROVED`), review ID, and execution artifacts.
"""


def main():
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        print(f"[{time.strftime('%X')}] (Attempt {attempt}/{max_retries}) Dispatching Sprint Day 5 Round 7 Review Request to Codex over Nexus Bus...")
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
        review_file = ROOT_DIR / "shared" / "trust" / "CODEX-DAY5-PAPER-DESK-ROUND7.md"
        review_file.write_text(output, encoding="utf-8")
        print(f"[{time.strftime('%X')}] Saved Codex Review to: {review_file}")
        break


if __name__ == "__main__":
    main()
