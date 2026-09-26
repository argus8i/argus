"""
research/trust/schemas.py
=========================
P0 record schemas (trust plan v2) and the append-only, hash-chained review ledger.

A review record approves one exact commit delta, parent_commit..reviewed_commit, identified by the reviewed tree and
the SHA-256 of the governed-scope patch; the reviewer may not be the author; test evidence is retained (command, exit
code, artifact, output hash). The ledger is JSONL: each line carries prev_record_sha256 (the previous line's
record_sha256, "" for the first) and record_sha256 (SHA-256 of the canonical record without that field), so an edited,
reordered or deleted line breaks the chain.

Known limit: identities (reviewer, author) are declared, not signed. The chain detects later edits, not impersonation.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping

REVIEW_SCHEMA_VERSION = 1
VERDICTS = ("APPROVED", "REJECTED", "CHANGES_REQUIRED")
_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}([+-]\d{2}:\d{2}|Z)$")
REVIEW_FIELDS = ("schema_version", "review_id", "reviewer", "author", "reviewed_commit", "parent_commit", "tree_sha",
                 "patch_sha256", "scope", "verdict", "reviewed_at", "tests", "failing_first_tests", "findings")


class SchemaError(ValueError):
    pass


class LedgerError(RuntimeError):
    pass


def validate_review(rec: Mapping[str, Any]) -> None:
    missing = [f for f in REVIEW_FIELDS if f not in rec]
    if missing:
        raise SchemaError(f"review record missing fields {missing}")
    if rec["schema_version"] != REVIEW_SCHEMA_VERSION:
        raise SchemaError(f"unknown review schema_version {rec['schema_version']!r}")
    for f in ("review_id", "reviewer", "author"):
        if not isinstance(rec[f], str) or not rec[f].strip():
            raise SchemaError(f"{f} must be a non-empty string")
    if rec["reviewer"].strip().lower() == rec["author"].strip().lower():
        raise SchemaError("self-review: reviewer and author are the same")
    for f in ("reviewed_commit", "parent_commit", "tree_sha"):
        if not isinstance(rec[f], str) or not _SHA1.match(rec[f]):
            raise SchemaError(f"{f} must be a full 40-hex git id")
    if not isinstance(rec["patch_sha256"], str) or not _SHA256.match(rec["patch_sha256"]):
        raise SchemaError("patch_sha256 must be 64 hex")
    if rec["verdict"] not in VERDICTS:
        raise SchemaError(f"verdict must be one of {VERDICTS}")
    if not isinstance(rec["reviewed_at"], str) or not _TIME.match(rec["reviewed_at"]):
        raise SchemaError("reviewed_at must be an ISO time with a UTC offset")
    if not isinstance(rec["scope"], list) or not rec["scope"]:
        raise SchemaError("scope must be a non-empty list of path globs")
    tests = rec["tests"]
    if not isinstance(tests, list) or not tests:
        raise SchemaError("tests must list at least one executed test with retained evidence")
    for t in tests:
        if not isinstance(t, Mapping) or not {"command", "exit_code", "artifact", "output_sha256"} <= set(t):
            raise SchemaError("each test needs command, exit_code, artifact, output_sha256")
        if not _SHA256.match(str(t["output_sha256"])):
            raise SchemaError("test output_sha256 must be 64 hex")
    if rec["verdict"] == "APPROVED" and not rec["failing_first_tests"]:
        raise SchemaError("an APPROVED review must cite failing-first tests (Rule 8 v2)")


def _canonical(rec: Mapping[str, Any]) -> bytes:
    return json.dumps({k: v for k, v in rec.items() if k != "record_sha256"}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def record_hash(rec: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical(rec)).hexdigest()


def read_ledger(path: Path) -> List[Dict[str, Any]]:
    """Every record, chain-verified. A missing file is an empty ledger; anything malformed raises LedgerError."""
    path = Path(path)
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    prev = ""
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"{path.name} line {n}: not JSON ({exc})") from exc
        if rec.get("prev_record_sha256") != prev:
            raise LedgerError(f"{path.name} line {n}: chain broken (a record was edited, removed or reordered)")
        if rec.get("record_sha256") != record_hash(rec):
            raise LedgerError(f"{path.name} line {n}: record hash does not match its content")
        try:
            validate_review(rec)
        except SchemaError as exc:
            raise LedgerError(f"{path.name} line {n}: {exc}") from exc
        prev = rec["record_sha256"]
        out.append(rec)
    return out


def append_ledger(path: Path, rec: Mapping[str, Any]) -> Dict[str, Any]:
    path = Path(path)
    existing = read_ledger(path)
    if any(r["review_id"] == rec.get("review_id") for r in existing):
        raise LedgerError(f"duplicate review_id {rec.get('review_id')!r}")
    new = {k: v for k, v in rec.items() if k not in ("prev_record_sha256", "record_sha256")}
    new["prev_record_sha256"] = existing[-1]["record_sha256"] if existing else ""
    validate_review(new)
    new["record_sha256"] = record_hash(new)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(new, sort_keys=True, ensure_ascii=False) + "\n")
    return new
