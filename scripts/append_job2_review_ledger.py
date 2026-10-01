import sys, json, hashlib
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, r"C:\Users\yashw\swing-trades-track2")
from research.trust.schemas import read_ledger, append_ledger, validate_review
from research.trust.review_check import describe

repo = Path(r"C:\Users\yashw\swing trades")
claude_worktree = Path(r"C:\Users\yashw\swing-trades-claude-004")
log_file = repo / "shared" / "trust" / "artifacts" / "CODEX-T2-01-JOB2-995AE27_tests.log"
log_sha256 = hashlib.sha256(log_file.read_bytes()).hexdigest()

scope = [
    "research/framework/market.py",
    "research/data/snapshot.py",
    "research/tests/test_codex_t2_4be7563_store.py",
    "research/tests/test_claude_t2_01_evidence_store_processes.py",
    "research/tests/test_claude_snapshot_rename_retry.py"
]

desc = describe(claude_worktree, "4be7563f28d6ab8e8aeb76c82de2991e55f8e674", "995ae27d561e24410d3fb5403381f77225f1cc0a", scope)

IST = timezone(timedelta(hours=5, minutes=30))
now_ist = datetime.now(IST).isoformat(timespec="seconds")

test_files = [
    "research/tests/test_claude_t2_01_evidence_store_processes.py",
    "research/tests/test_codex_t2_4be7563_store.py",
    "research/tests/test_codex_t2_147c2a9_retention.py",
    "research/tests/test_codex_t2_1580779_pin_race.py",
    "research/tests/test_codex_t2_01_lock_recheck.py",
    "research/tests/test_codex_t2_01_recheck_probes.py",
    "research/tests/test_claude_t2_01_surveillance_session.py",
    "research/tests/test_claude_snapshot_rename_retry.py"
]
cmd = f"python -m pytest {' '.join(test_files)} -v -p no:cacheprovider"

record = {
    "schema_version": 1,
    "review_id": "CODEX-T2-01-JOB2-995AE27",
    "reviewer": "codex",
    "author": "claude",
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
        "research/tests/test_codex_t2_4be7563_store.py::test_publication_does_not_overwrite_file_created_after_initial_check",
        "research/tests/test_claude_snapshot_rename_retry.py::test_a_brief_permission_error_on_the_final_rename_is_retried"
    ],
    "findings": [
        "Scoped code-review approval for publication-race repair and snapshot rename retry at 995ae27",
        "os.link publish-if-absent verified, competing publication hash-validated, randomized temp names, unsupported links fail closed",
        "Recorded limitation: cleanup is best effort (write before try/finally), subprocess start barrier is approximate, snapshot.create uses shared partial dir"
    ]
}

saved = append_ledger(repo / "shared" / "trust" / "reviews.jsonl", record)
print("SUCCESSFULLY APPENDED:", saved["review_id"], saved["verdict"], saved["record_sha256"])

verified_ledger = read_ledger(repo / "shared" / "trust" / "reviews.jsonl")
print(f"Total records in ledger now: {len(verified_ledger)}")
print("Last record verdict:", verified_ledger[-1]["verdict"])
