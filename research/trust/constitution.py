"""
research/trust/constitution.py
==============================
The Constitution (CONSTITUTION.md at the repository root) changes only with Yashu's approval. This module makes that
visible to every agent, every day:

  governance/approvals.jsonl   hash-chained record of each approval: the Constitution's SHA-256, Yashu's own words,
                               where and when he said them, and which agent recorded them.
  check()                      PASS when the file matches the last approval; UNREVIEWED while it is a DRAFT (never
                               approved); FAIL when it was changed without a new approval, when the approval ledger
                               was edited, or when another working copy of the file differs.

Honest limit: agents have shell access to this machine, so no file can be made physically unwritable. What this
guarantees is that a change cannot go UNNOTICED: the trust status fails for everyone until Yashu approves the new text,
and git history shows who changed what.

    python -m research.trust.constitution status
    python -m research.trust.constitution approve --quote "<Yashu's exact words>" --where "<chat, date, time>" --recorded-by claude
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

IST = timezone(timedelta(hours=5, minutes=30))
FILE = "CONSTITUTION.md"
LEDGER = Path("governance") / "approvals.jsonl"
RECORDERS = ("claude", "codex", "antigravity", "yashu")


class ApprovalError(ValueError):
    """An approval record that does not carry Yashu's words, the place, or a known recorder."""


class LedgerBroken(RuntimeError):
    """The approval ledger's hash chain does not verify (an old record was edited or removed)."""


def constitution_sha(root: Path) -> Optional[str]:
    p = Path(root) / FILE
    if not p.exists():
        return None
    return hashlib.sha256(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _canon(rec: Dict[str, Any]) -> bytes:
    body = {k: v for k, v in rec.items() if k != "record_sha256"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def read_ledger(root: Path) -> List[Dict[str, Any]]:
    p = Path(root) / LEDGER
    if not p.exists():
        return []
    rows, prev = [], ""
    for i, ln in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("prev_record_sha256") != prev or hashlib.sha256(_canon(r)).hexdigest() != r.get("record_sha256"):
            raise LedgerBroken(f"approval ledger broken at line {i} of {p}")
        prev = r["record_sha256"]
        rows.append(r)
    return rows


def record_approval(root: Path, *, quote: str, where: str, recorded_by: str,
                    now: Optional[datetime] = None) -> Dict[str, Any]:
    """Record that Yashu approved the CURRENT text. Only ever with his own words and where he said them."""
    if not str(quote).strip():
        raise ApprovalError("Yashu's own words are required (quote)")
    if not str(where).strip():
        raise ApprovalError("where and when he said it is required (where)")
    if recorded_by not in RECORDERS:
        raise ApprovalError(f"recorded_by must be one of {RECORDERS}")
    sha = constitution_sha(root)
    if sha is None:
        raise ApprovalError(f"{FILE} does not exist")
    rows = read_ledger(root)
    rec = {"kind": "CONSTITUTION_APPROVAL", "sha256": sha, "approved_by": "Yashu", "quote": str(quote).strip(),
           "where": str(where).strip(), "recorded_by": recorded_by,
           "recorded_at": (now or datetime.now(IST)).isoformat(timespec="seconds"),
           "prev_record_sha256": rows[-1]["record_sha256"] if rows else ""}
    rec["record_sha256"] = hashlib.sha256(_canon(rec)).hexdigest()
    p = Path(root) / LEDGER
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(rec, sort_keys=True, ensure_ascii=False) + "\n")
    return rec


def check(root: Path, other_roots: Sequence[Path] = ()) -> Tuple[str, List[str]]:
    sha = constitution_sha(root)
    if sha is None:
        return "MISSING", [f"{FILE} does not exist in {root}"]
    try:
        rows = read_ledger(root)
    except (LedgerBroken, ValueError) as exc:
        return "FAIL", [f"approval ledger: {exc}"]
    fails: List[str] = []
    for other in other_roots:
        o = constitution_sha(other)
        if o is None:
            fails.append(f"{FILE} is missing in the other working copy {other}")
        elif o != sha:
            fails.append(f"{FILE} in {other} differs from {root} ({o[:12]} vs {sha[:12]})")
    approvals = [r for r in rows if r.get("kind") == "CONSTITUTION_APPROVAL"]
    if not approvals:                     # a draft is not in force, so unsynced copies are noted, not failed
        return "UNREVIEWED", [f"DRAFT: {FILE} ({sha[:12]}) has never been approved by Yashu; it is not in force "
                              "yet"] + fails
    last = approvals[-1]
    bad = [k for k in ("quote", "where") if not str(last.get(k, "")).strip()]
    if last.get("approved_by") != "Yashu" or last.get("recorded_by") not in RECORDERS or bad:
        fails.insert(0, f"the last approval record is incomplete (fields {bad}, approved_by "
                        f"{last.get('approved_by')!r}, recorded_by {last.get('recorded_by')!r})")
    if last.get("sha256") != sha:
        fails.insert(0, f"{FILE} changed without Yashu's approval (approved {str(last.get('sha256'))[:12]}, "
                        f"now {sha[:12]})")
    if fails:
        return "FAIL", fails
    return "PASS", [f"approved by Yashu ({last['where']}): \"{last['quote']}\"; recorded by {last['recorded_by']}"]


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Constitution approval record and check")
    ap.add_argument("cmd", choices=["status", "approve"])
    ap.add_argument("--quote", default="")
    ap.add_argument("--where", default="")
    ap.add_argument("--recorded-by", default="")
    a = ap.parse_args(argv)
    root = paths.repo_root()
    if a.cmd == "approve":
        try:
            rec = record_approval(root, quote=a.quote, where=a.where, recorded_by=a.recorded_by)
        except (ApprovalError, LedgerBroken) as exc:
            print(f"REFUSED: {exc}")
            return 2
        print(json.dumps(rec, indent=1, ensure_ascii=False))
        return 0
    others = [p for p in {paths.main_checkout()} if Path(p).resolve() != Path(root).resolve()]
    st, details = check(root, others)
    print(st)
    for d in details:
        print(" ", d)
    return {"PASS": 0, "UNREVIEWED": 2, "MISSING": 3, "FAIL": 1}[st]


if __name__ == "__main__":
    raise SystemExit(main())
