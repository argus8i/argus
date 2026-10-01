"""
scripts/append_day2_review_ledger.py
===================================
Appends the formally approved Sprint Day 2 review record to shared/trust/reviews.jsonl.
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

log_file = repo / "shared" / "trust" / "artifacts" / "DAY2-EXECUTION-RISK-GOVERNOR-TESTS.log"
log_bytes = log_file.read_bytes()
log_sha256 = hashlib.sha256(log_bytes).hexdigest()

scope = [
    "antigravity/engine/execution_simulator.py",
    "antigravity/engine/risk_governor.py",
    "tests/test_execution_risk_governor.py"
]

desc = describe(repo, "2bc6503", "95390f4", scope)

IST = timezone(timedelta(hours=5, minutes=30))
now_ist = datetime.now(IST).isoformat(timespec="seconds")

cmd = r".venv\Scripts\python.exe -m pytest tests/test_day1_data_contracts.py tests/test_execution_risk_governor.py -v"

record = {
    "schema_version": 1,
    "review_id": "CODEX-DAY2-EXECUTION-RISK-95390F4",
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
            "output_sha256": log_sha256
        }
    ],
    "failing_first_tests": [
        "tests/test_execution_risk_governor.py::test_codex_round4_partial_fill_precision_and_post_fill_risk_cap"
    ],
    "findings": [
        "Finding 1 resolved: exact boundary case records Rs 1,500.00 open risk, full weighted-entry precision, matching pre-check and ledger calculations.",
        "Independently executed all 45 execution/risk tests: 45 passed, exit code 0.",
        "Failing-first acceptance test verified on parent commit.",
        "Partial-exit risk consistent with residual notional and stop basis. Findings 2-4 remain accepted."
    ]
}

saved = append_ledger(repo / "shared" / "trust" / "reviews.jsonl", record)
print("SUCCESSFULLY APPENDED:", saved["review_id"], saved["verdict"], saved["record_sha256"])

verified_ledger = read_ledger(repo / "shared" / "trust" / "reviews.jsonl")
print(f"Total records in ledger now: {len(verified_ledger)}")
print("Last record review_id:", verified_ledger[-1]["review_id"])
print("Last record verdict:", verified_ledger[-1]["verdict"])
