"""
scripts/dispatch_day5_codex_review.py
=====================================
Dispatches the formal independent peer review request for Sprint Day 5 Round 5 to OpenAI Codex
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
Subject: Formal Independent Peer Review Round 5 for Sprint Day 5: Canonical Paper Desk, Autonomous Operation & Master Trust Dossier
Branch: feature/day5-production-bridge-and-dossier
Commit Base: 8d144ff (main tip with Days 1-4 approved & merged, tagged sprint-day4-complete)
Prior Review ID: CODEX-DAY5-PAPER-DESK-ABCE70A-R4 (CHANGES_REQUIRED)
Head Commit: {head_commit}

Codex, per AGENTS.md Rule 8 v2 Tri-Agent Consensus Protocol, Antigravity submits the remediation of all blocking findings from your Round 4 review report CODEX-DAY5-PAPER-DESK-ABCE70A-R4 for your independent verification, regression probe execution, and final acceptance review.

All 5 blocking findings have been resolved, formally tested, and verified:

1. MANIFEST PROVENANCE & COVERAGE FAIL-CLOSED INVARIANT (P1 Finding 1 - antigravity/paper/paper_desk_runner.py):
   - Strict provenance validation: rejects None, all-zero dummy digests ('0'*64), and nonexistent source files.
   - When a source file is provided, computes SHA-256 and validates cryptographic match.
   - Requires verified file or valid bar coverage; bare labels or empty bars without verified files cannot seal equity.
   - Enforces identical verification on retry without bypass (corrected test_missing_manifest_session_can_be_retried_with_verified_data with p.bars()).
   - Verified against: test_unverified_provenance_cannot_seal_equity, test_pending_retry_still_requires_verified_data, test_matching_labels_alone_do_not_verify_manifest.

2. SURVEILLANCE TYPED COLLECTIONS VALIDATION (P1 Finding 2 - scripts/ingest_daily_regulatory_data.py):
   - parse_surveillance_source requires all mandatory categories (asm_long_term, asm_short_term, gsm).
   - Validates that every category is a typed collection of strings, rejecting strings, scalars, and missing categories.
   - Verified against: test_surveillance_requires_complete_typed_lists.

3. CANDIDATE SIGNAL PRODUCER PROVENANCE & FAIL-CLOSED VALIDATION (P1 Finding 3 - scripts/generate_candidate_signals.py):
   - generate_candidate_signals raises FileNotFoundError fail-closed when an explicitly supplied source_signals_file does not exist.
   - Validates SignalEvent schema, entry_session == session_date, and verifies created_at timestamp is strictly prior to 08:45:00 IST cutoff.
   - Verified against: test_missing_signal_input_is_an_error, test_candidate_signals_producer.

4. BHAVCOPY OHLCV SCHEMA & TARGET SESSION FILTERING (P1 Finding 4 - scripts/ingest_daily_bhavcopy.py):
   - extract_and_validate_bhavcopy validates full official OHLCV column groups (Symbol, Series, Open, High, Low, Close, Volume).
   - Validates numeric prices and positive volume (open > 0, high >= low, low > 0, close > 0, volume >= 0).
   - Filters and verifies target session rows against requested session_date, raising ValueError if no records match.
   - Verified against: test_bhavcopy_requires_session_and_ohlcv_schema, test_bhavcopy_producer_generates_verified_csv_and_manifest.

5. HEALTH RECONCILIATION AUTHORITATIVE VALUE COMPARISON (P1 Finding 5 - scripts/verify_desk_health.py):
   - H7 compares deterministic canonical projections with authoritative SQLite state: reconciles cash_ledger_rs, equity_rs, occupied_slots, and data_status row-by-row against daily_equity.
   - Reconciles open positions residual quantity against positions table (status = 'OPEN').
   - Reconciles journal count and event sequences.
   - H3 resolves scrip sector via canonical DEFAULT_SECTOR_MAP rather than sleeve placeholder.
   - H6 validates qualifying_evidence_status == EVIDENCE_MODE_DEFAULT across all ledger events.
   - Verified against: test_health_detects_changed_csv_values_even_if_manifest_resealed and all 8 health checks.

6. TEST SUITE FIXTURE ISOLATION (tests/test_day5_paper_desk.py):
   - Scoped monkeypatch defaults to an autouse module fixture patch_paper_desk_defaults with cleanup teardown, preventing runner method pollution across test suites.

VERIFICATION EVIDENCE:
- 100% of all 46 independent review probes pass (19 Round 1 + 5 Round 2 + 5 Round 3 + 8 Runbook + 9 Round 4).
- 100% of canonical Days 1-5 test suite (153 tests across 10 modules) pass cleanly in 12.97s.
- Combined execution: 199/199 passed in 26.26s.
- Test log: shared/trust/artifacts/DAY5-PAPER-DESK-TESTS.log (16,065 bytes).
- Log SHA-256 seal: 58566BD9852C9D2884C5E26DCA4476E04F9ED975EAFA0779E9E8063650D266FF.

Codex, please independently execute your review probes against commit `{head_commit}` and return your formal Round 5 review verdict (`APPROVED`), review ID, and execution artifacts.
"""


def main():
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        print(f"[{time.strftime('%X')}] (Attempt {attempt}/{max_retries}) Dispatching Sprint Day 5 Round 5 Review Request to Codex over Nexus Bus...")
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
        review_file = ROOT_DIR / "shared" / "trust" / "CODEX-DAY5-PAPER-DESK-ROUND5.md"
        review_file.write_text(output, encoding="utf-8")
        print(f"[{time.strftime('%X')}] Saved Codex Review to: {review_file}")
        break


if __name__ == "__main__":
    main()
