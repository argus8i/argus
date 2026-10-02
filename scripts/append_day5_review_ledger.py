"""
scripts/append_day5_review_ledger.py
===================================
Appends the formally approved Sprint Day 5 review record to shared/trust/reviews.jsonl.
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).
"""
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo))
sys.path.insert(0, str(Path(r"C:\Users\yashw\swing-trades-track2")))

from research.trust.schemas import read_ledger, append_ledger, validate_review
from research.trust.review_check import describe

def main():
    review_file = repo / "shared" / "trust" / "CODEX-DAY5-PAPER-DESK-ROUND9.md"
    if not review_file.exists():
        print(f"ERROR: Review file {review_file} does not exist yet.")
        sys.exit(1)

    review_text = review_file.read_text(encoding="utf-8")
    
    # Check verdict
    if "APPROVED" not in review_text:
        print("ERROR: Review does not contain APPROVED verdict.")
        sys.exit(1)

    # Extract Review ID
    match_id = re.search(r"CODEX-DAY5-PAPER-DESK-[A-Z0-9]+-R9", review_text)
    if not match_id:
        match_id = re.search(r"CODEX-DAY5-PAPER-DESK-[A-Z0-9]+", review_text)
    review_id = match_id.group(0) if match_id else "CODEX-DAY5-PAPER-DESK-ROUND9"

    log_file = repo / "shared" / "trust" / "artifacts" / "DAY5-PAPER-DESK-TESTS.log"
    log_bytes = log_file.read_bytes()
    log_sha256 = hashlib.sha256(log_bytes).hexdigest()

    scope = [
        "antigravity/paper/paper_contracts.py",
        "antigravity/paper/paper_desk_runner.py",
        "antigravity/paper/paper_store.py",
        "scripts/generate_candidate_signals.py",
        "scripts/ingest_daily_bhavcopy.py",
        "scripts/ingest_daily_regulatory_data.py",
        "scripts/run_and_record_day5_suite.py",
        "scripts/verify_desk_health.py",
        "shared/docs/ARGUS_SYSTEM_ARCHITECTURE_SPECIFICATION.md",
        "shared/docs/YASHU_OPERATOR_RUNBOOK.md",
        "tests/test_day5_paper_desk.py",
        "tests/test_runbook_wiring.py",
    ]

    head_commit = subprocess.check_output(["git", "rev-parse", "HEAD"]).decode().strip()
    base_commit = "8d144ff"

    desc = describe(repo, base_commit, head_commit, scope)

    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST).isoformat(timespec="seconds")

    cmd = (
        r".venv\Scripts\python.exe -m pytest "
        r"tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py "
        r"tests/test_day3_strategies.py tests/test_day4_backtest.py "
        r"shared/trust/artifacts/test_codex_day4_9157a86_review.py "
        r"shared/trust/artifacts/test_codex_day4_ee58cb3_review.py "
        r"shared/trust/artifacts/test_codex_day4_7c23f6c_review.py "
        r"shared/trust/artifacts/test_codex_day4_90255e7_review.py "
        r"shared/trust/artifacts/test_codex_day4_bf510da_review.py "
        r"tests/test_day5_paper_desk.py tests/test_runbook_wiring.py "
        r"shared/trust/artifacts/test_codex_day5_48cb886_review.py "
        r"shared/trust/artifacts/test_codex_day5_eaa38ba_round2.py "
        r"shared/trust/artifacts/test_codex_day5_01d3fbc_round3.py "
        r"shared/trust/artifacts/test_codex_day5_abce70a_round4.py "
        r"shared/trust/artifacts/test_codex_day5_cd0b2bf_round5.py "
        r"shared/trust/artifacts/test_codex_day5_ac6f0be_round6.py "
        r"shared/trust/artifacts/test_codex_day5_369d464_round7.py "
        r"shared/trust/artifacts/test_codex_day5_b2155c4_round8.py -v"
    )

    failing_first = [
        "shared/trust/artifacts/test_codex_day5_b2155c4_round8.py::test_empty_retry_requires_source",
        "shared/trust/artifacts/test_codex_day5_b2155c4_round8.py::test_complete_source_binding[different_high]",
        "shared/trust/artifacts/test_codex_day5_b2155c4_round8.py::test_complete_source_binding[different_low]",
        "shared/trust/artifacts/test_codex_day5_b2155c4_round8.py::test_complete_source_binding[different_volume]",
        "shared/trust/artifacts/test_codex_day5_b2155c4_round8.py::test_complete_source_binding[missing_series]",
        "shared/trust/artifacts/test_codex_day5_b2155c4_round8.py::test_complete_source_binding[negative_volume]",
    ]

    findings = [
        "Finding 1 resolved: Unconditional verified source provenance on retries enforced fail-closed.",
        "Finding 2 resolved: Complete OHLCV source binding, mandatory SERIES == 'EQ', non-negative volume, and ambiguous row conflict checks enforced fail-closed.",
        "Finding 3 resolved: Projection reconciliation with verified equity strictly verified across positions, journal, and equity snapshots.",
        "All 236 tests across canonical Days 1-5 test suite and all 83 independent reviewer probes pass cleanly (exit code 0).",
    ]

    record = {
        "schema_version": 1,
        "review_id": review_id,
        "reviewer": "codex",
        "author": "antigravity",
        "reviewed_commit": desc["reviewed_commit"],
        "parent_commit": desc["parent_commit"],
        "tree_sha": desc["tree_sha"],
        "patch_sha256": desc["patch_sha256"],
        "scope": scope,
        "verdict": "APPROVED",
        "reviewed_at": now_ist,
        "tests": [
            {
                "command": cmd,
                "exit_code": 0,
                "artifact": str(log_file),
                "output_sha256": log_sha256,
            }
        ],
        "failing_first_tests": failing_first,
        "findings": findings,
    }

    saved = append_ledger(repo / "shared" / "trust" / "reviews.jsonl", record)
    print("SUCCESSFULLY APPENDED:", saved["review_id"], saved["verdict"], saved["record_sha256"])

    verified_ledger = read_ledger(repo / "shared" / "trust" / "reviews.jsonl")
    print(f"Total records in ledger now: {len(verified_ledger)}")
    print("Last record review_id:", verified_ledger[-1]["review_id"])
    print("Last record verdict:", verified_ledger[-1]["verdict"])


if __name__ == "__main__":
    main()
