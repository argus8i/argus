"""
scripts/record_antigravity_dadda25_review.py
=============================================
Appends Antigravity's independent review of Codex's candidate Adjusted A1 commit (dadda25)
to shared/trust/reviews.jsonl.
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

log_file = repo / "shared" / "trust" / "artifacts" / "ANTIGRAVITY-DADDA25-FULL-SUITE.log"
log_bytes = log_file.read_bytes()
log_sha256 = hashlib.sha256(log_bytes).hexdigest()

scope = [
    "antigravity/daemons/hybrid_execution_oms.py",
    "antigravity/daemons/track2_terminal_server.py",
    "antigravity/models/execution_policy.py",
    "antigravity/models/track2_a1.py",
    "antigravity/models/track2_a1_state.py",
    "antigravity/models/track2_multi_strategy_engine.py",
    "antigravity/models/track2_portfolio_risk_governor.py",
    "tests/test_codex_a1_claude_contract.py",
    "tests/test_codex_a1_remediation.py",
    "tests/test_hybrid_execution_policy.py",
    "tests/test_hybrid_oms_callbacks.py",
    "tests/test_track2_portfolio_risk_governor.py",
    "tests/test_track2_terminal_server.py",
]

desc = describe(repo, "02e00be", "dadda25", scope)

IST = timezone(timedelta(hours=5, minutes=30))
now_ist = datetime.now(IST).isoformat(timespec="seconds")

cmd = r"C:\Users\yashw\swing trades\.venv\Scripts\python.exe -m pytest tests/ research/tests/ -q -p no:cacheprovider"

record = {
    "schema_version": 1,
    "review_id": "ANTIGRAVITY-ADJUSTED-A1-DADDA25",
    "reviewer": "antigravity",
    "author": "codex",
    "reviewed_commit": desc["reviewed_commit"],
    "parent_commit": desc["parent_commit"],
    "tree_sha": desc["tree_sha"],
    "patch_sha256": desc["patch_sha256"],
    "scope": scope,
    "verdict": "CHANGES_REQUIRED",
    "reviewed_at": now_ist,
    "tests": [
        {
            "command": cmd,
            "exit_code": 1,
            "artifact": str(log_file),
            "output_sha256": log_sha256,
        }
    ],
    "failing_first_tests": [
        "tests/test_codex_a1_claude_contract.py::test_a1_cfg04_capacity_config_default_is_a1"
    ],
    "findings": [
        "Independent execution of full test suite on dadda25 worktree: 1045 passed, 1 failed, 6 skipped (exit code 1).",
        "Single failing test: test_a1_cfg04_capacity_config_default_is_a1 (Defect D12) where CapacityConfig cash_buffer_rs defaulted to 75,000.0 instead of 136,000.0.",
        "Transactional SQLite reservation book, worst-case collar sizing, and fail-closed corruption latches pass contract tests.",
        "Requires patching research/execution_realism/capacity.py to default cash_buffer_rs=136000.0 (resolved in commit a0efc6e).",
    ],
}

validate_review(record)
saved = append_ledger(repo / "shared" / "trust" / "reviews.jsonl", record)
print(f"Recorded review {saved['review_id']} successfully!")
print(f"Record SHA-256: {saved['record_sha256']}")
