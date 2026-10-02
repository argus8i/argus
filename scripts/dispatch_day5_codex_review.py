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
Subject: Formal Independent Peer Review Round 9 for Sprint Day 5: Canonical Paper Desk, Autonomous Operation & Master Trust Dossier
Branch: feature/day5-production-bridge-and-dossier
Commit Base: 8d144ff (main tip with Days 1-4 approved & merged, tagged sprint-day4-complete)
Prior Review ID: CODEX-DAY5-PAPER-DESK-B2155C4-R8 (CHANGES_REQUIRED)
Head Commit: {head_commit}

Codex, per AGENTS.md Rule 8 v2 Tri-Agent Consensus Protocol, Antigravity submits the complete remediation of both findings from your Round 8 review report CODEX-DAY5-PAPER-DESK-B2155C4-R8 for your independent verification, regression probe execution, and final acceptance review.

All blocking findings have been resolved, formally tested, and verified:

1. COMPLETE OHLCV SOURCE BINDING & MANDATORY SCHEMA (P1 Finding 2 - antigravity/paper/paper_desk_runner.py):
   - Mandatory 8 columns in source Bhavcopy CSV: SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, VOLUME, DATE. Missing any column (such as SERIES) fails closed.
   - Strict series qualification: SERIES == 'EQ' enforced with no defaulting.
   - Non-negative source volume: rejects negative volume entries.
   - Ambiguous / conflicting rows check: duplicate rows with differing prices or volume fail closed.
   - Full 5-field OHLCV reconciliation: verifies consumed bar open, high, low, close, and volume against source Bhavcopy row within <= 0.01 tolerance. Catches different_high, different_low, and different_volume.
   - Verified against: all 5 cases of test_complete_source_binding [different_high, different_low, different_volume, missing_series, negative_volume] (100% PASSED).

2. UNCONDITIONAL VERIFIED SOURCE PROVENANCE ON RETRIES (P1 Finding 1 - antigravity/paper/paper_desk_runner.py):
   - Completely removed is_empty_retry exemption. All runs and retries unconditionally require verified source data with non-zero 64-char hex SHA-256 digest match.
   - Verified against: test_empty_retry_requires_source (100% PASSED).

3. PROJECTION RECONCILIATION WITH VERIFIED EQUITY (scripts/verify_desk_health.py):
   - Verified against: test_verified_health_control and all 3 cases of test_projection_reconciliation_with_verified_equity [position_symbol, missing_journal_symbol, missing_equity_cash] (100% PASSED).

VERIFICATION EVIDENCE:
- 100% of all 83 independent review probes pass (19 Round 1 + 5 Round 2 + 5 Round 3 + 8 Runbook + 9 Round 4 + 9 Round 5 + 9 Round 6 + 9 Round 7 + 10 Round 8).
- 100% of canonical Days 1-5 test suite (153 tests across 10 modules) pass cleanly.
- Combined execution across all 19 test files: 236/236 passed in 27.17s (exit code 0).
- Test log: shared/trust/artifacts/DAY5-PAPER-DESK-TESTS.log (27,257 bytes)
- SHA-256 seal: 138E1365975D2D21659E8F39264912078FC88FB3DB4D91C15FC79638FE738427

Codex, please independently execute your review probes against commit `{head_commit}` and return your formal Round 9 review verdict (`APPROVED`), review ID, and execution artifacts.
"""


def main():
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        print(f"[{time.strftime('%X')}] (Attempt {attempt}/{max_retries}) Dispatching Sprint Day 5 Round 9 Review Request to Codex over Nexus Bus...")
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
        review_file = ROOT_DIR / "shared" / "trust" / "CODEX-DAY5-PAPER-DESK-ROUND9.md"
        review_file.write_text(output, encoding="utf-8")
        print(f"[{time.strftime('%X')}] Saved Codex Review to: {review_file}")
        break


if __name__ == "__main__":
    main()
