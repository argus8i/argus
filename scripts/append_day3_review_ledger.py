"""
scripts/append_day3_review_ledger.py
===================================
Appends the formally approved Sprint Day 3 review record to shared/trust/reviews.jsonl.
Part of Project Swing Trades (ARGUS 8i Track 2 Liquid Desk).
"""
import hashlib
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

repo = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(repo))
sys.path.insert(0, str(Path(r"C:\Users\yashw\swing-trades-track2")))

from research.trust.schemas import read_ledger, append_ledger, validate_review
from research.trust.review_check import describe

log_file = repo / "shared" / "trust" / "artifacts" / "DAY3-ALPHA-STRATEGIES-TESTS.log"
log_bytes = log_file.read_bytes()
log_sha256 = hashlib.sha256(log_bytes).hexdigest()

scope = [
    "antigravity/strategies/base_strategy.py",
    "antigravity/strategies/delivery_accumulation.py",
    "antigravity/strategies/high52_momentum.py",
    "antigravity/strategies/expiry_relief.py",
    "shared/track2_liquid/strategies/specs/delivery_accumulation_v1.yaml",
    "shared/track2_liquid/strategies/specs/high52_momentum_v1.yaml",
    "shared/track2_liquid/strategies/specs/expiry_relief_v1.yaml",
    "shared/track2_liquid/strategies/specs/SPEC_MANIFEST.sha256",
    "shared/track2_liquid/strategies/readiness_audit.md",
    "tests/test_day3_strategies.py",
]

desc = describe(repo, "dd558f2", "815a18c", scope)

IST = timezone(timedelta(hours=5, minutes=30))
now_ist = datetime.now(IST).isoformat(timespec="seconds")

cmd = r".venv\Scripts\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py tests/test_day3_strategies.py -v"

record = {
    "schema_version": 1,
    "review_id": "CODEX-DAY3-ALPHA-STRATEGIES-815A18C",
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
    "failing_first_tests": [
        "tests/test_day3_strategies.py::test_codex_round6_unicode_trace_preservation",
        "tests/test_day3_strategies.py::test_codex_round6_timezone_name_normalization",
    ],
    "findings": [
        "Both Round 6 blockers closed: exact built-in strings preserve identity and Unicode text.",
        "String subclasses become built-in strings without invoking overridden __str__ or encode.",
        "Timezone names detached from mutable string subclasses and normalized to built-in strings.",
        "All three specification hashes match the committed manifest (SPEC_MANIFEST.sha256).",
        "Independent read-only verification reproduced regression failures against 468b89c and passes against 815a18c.",
        "All 93 tests in the full test suite pass cleanly (exit code 0).",
    ],
}

saved = append_ledger(repo / "shared" / "trust" / "reviews.jsonl", record)
print("SUCCESSFULLY APPENDED:", saved["review_id"], saved["verdict"], saved["record_sha256"])

verified_ledger = read_ledger(repo / "shared" / "trust" / "reviews.jsonl")
print(f"Total records in ledger now: {len(verified_ledger)}")
print("Last record review_id:", verified_ledger[-1]["review_id"])
print("Last record verdict:", verified_ledger[-1]["verdict"])
