"""
research/trust/review_check.py
==============================
T7: exact-commit review coverage (trust plan v2).

A commit that changes a governed file (code, config, data contracts; research/trust/inventory.json names the globs)
is REVIEWED only when an APPROVED review record in the hash-chained ledger covers it:
  - the commit lies in parent_commit..reviewed_commit of the record;
  - the record's tree_sha and patch_sha256 equal what git computes for that exact delta now;
  - the reviewer is not the author.
Anything else, including a later commit on top of a reviewed one, is UNREVIEWED.

    python -m research.trust.review_check describe <parent> <commit>    # fields a reviewer needs for a record
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True,
                          encoding="utf-8", errors="replace").stdout.strip()


def _git_bytes(repo: Path, *args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True).stdout


def _match(path: str, pattern: str) -> bool:
    p = PurePosixPath(path)
    if p.full_match(pattern):
        return True
    return pattern.startswith("**/") and p.full_match(pattern[3:])


def governed(path: str, scope: Sequence[str], excluded: Sequence[str] = ()) -> bool:
    path = path.replace("\\", "/")
    return any(_match(path, g) for g in scope) and not any(_match(path, g) for g in excluded)


def changed_files(repo: Path, a: str, b: str) -> List[str]:
    out = _git(repo, "diff", "--name-only", "--no-renames", a, b)
    return sorted(x for x in out.splitlines() if x.strip())


def describe(repo: Path, parent: str, commit: str, scope: Sequence[str], excluded: Sequence[str] = ()
             ) -> Dict[str, Any]:
    """The identity of the exact delta parent..commit: full ids, tree, governed files, and the SHA-256 of the
    governed-scope patch (full index, binary, no colour)."""
    parent_full, commit_full = _git(repo, "rev-parse", parent), _git(repo, "rev-parse", commit)
    files = [f for f in changed_files(repo, parent_full, commit_full) if governed(f, scope, excluded)]
    patch = _git_bytes(repo, "diff", "--full-index", "--binary", "--no-color", "--no-renames", parent_full,
                       commit_full, "--", *files) if files else b""
    return {"parent_commit": parent_full, "reviewed_commit": commit_full,
            "tree_sha": _git(repo, "rev-parse", f"{commit_full}^{{tree}}"),
            "patch_sha256": hashlib.sha256(patch).hexdigest(), "files": files}


def _valid_ranges(repo: Path, reviews: Iterable[Mapping[str, Any]], scope: Sequence[str],
                  excluded: Sequence[str]) -> List[set]:
    ranges = []
    for r in reviews:
        if r.get("verdict") != "APPROVED":
            continue
        if str(r.get("reviewer", "")).strip().lower() == str(r.get("author", "")).strip().lower():
            continue
        try:
            d = describe(repo, r["parent_commit"], r["reviewed_commit"], scope, excluded)
            commits = set(_git(repo, "rev-list", f"{d['parent_commit']}..{d['reviewed_commit']}").split())
        except (subprocess.CalledProcessError, KeyError):
            continue                                       # a record for commits this repo does not have covers nothing
        if d["tree_sha"] == r.get("tree_sha") and d["patch_sha256"] == r.get("patch_sha256"):
            ranges.append(commits)
    return ranges


def unreviewed_commits(repo: Path, base: str, head: str, reviews: Iterable[Mapping[str, Any]],
                       scope: Sequence[str], excluded: Sequence[str] = ()) -> List[Dict[str, Any]]:
    """Commits in base..head (oldest first) that change a governed file and no valid APPROVED review covers."""
    ranges = _valid_ranges(repo, reviews, scope, excluded)
    out = []
    for c in _git(repo, "rev-list", "--reverse", f"{base}..{head}").split():
        files = [f for f in changed_files(repo, f"{c}^", c) if governed(f, scope, excluded)]
        if files and not any(c in rg for rg in ranges):
            out.append({"commit": c, "subject": _git(repo, "log", "-1", "--format=%s", c), "files": files})
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths
    from research.trust.status import load_inventory

    ap = argparse.ArgumentParser(description="T7 exact-commit review helper")
    ap.add_argument("cmd", choices=["describe"])
    ap.add_argument("parent")
    ap.add_argument("commit")
    ap.add_argument("--repo", default=None)
    args = ap.parse_args(argv)
    inv = load_inventory()
    repo = Path(args.repo) if args.repo else paths.repo_root()
    print(json.dumps(describe(repo, args.parent, args.commit, inv["governed_scope"], inv["excluded_scope"]), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
