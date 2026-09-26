"""
research/framework/rules.py
===========================
Machine checks for the framework rules (research/framework/RULES.md). Each returns plain-language problems; an
empty list means the rule holds. The daily run refuses to plan for a strategy with any problem.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

# Written so this file does not match itself: each pattern needs a character the pattern text does not have there.
BROKER_PATTERNS = [
    r"\bdhan[h]q\b", r"\bkite[c]onnect\b", r"\bsmart[a]pi\b", r"\bupstox_[c]lient\b",
    r"\b(place|modify|cancel)[_]order\b", r"\brequests[.](post|put|delete)[(]", r"api[.]dhan[.]co",
    r"kite[.]zerodha[.]com/oms", r"\bbreeze_[c]onnect\b",
]
PAPER_FOLDER = "/shared/track2_liquid/paper/"


def paper_only_scan(files: Iterable[Path]) -> List[Dict[str, Any]]:
    """Lines of code (comments ignored) that look like broker access. AGENTS.md Rule 1: the desks are paper only."""
    pats = [re.compile(p, re.IGNORECASE) for p in BROKER_PATTERNS]
    hits = []
    for f in files:
        for i, line in enumerate(Path(f).read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            for p in pats:
                if p.search(code):
                    hits.append({"file": str(f), "line": i, "pattern": p.pattern, "text": line.strip()[:120]})
                    break
    return hits


def journal_location_problem(path: Path) -> Optional[str]:
    """Track 2 paper journals live only under shared/track2_liquid/paper/ (AGENTS.md Rule 11)."""
    p = "/" + Path(path).as_posix().lower().lstrip("/")
    if "track1" in p or "observation_log" in p or "/chatgpt/" in p:
        return f"{path}: Track 1 location; Track 2 paper journals never go there (Rule 11)"
    if PAPER_FOLDER not in p:
        return f"{path}: not under shared/track2_liquid/paper/"
    return None


def _git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=30)


def prereg_git_problem(prereg: Path) -> Optional[str]:
    """The pre-registration must be committed and unchanged in the research checkout."""
    from research.data import paths

    root = paths.repo_root()
    try:
        rel = Path(prereg).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return f"{prereg}: outside the research checkout"
    if _git(root, "ls-files", "--error-unmatch", "--", rel).returncode != 0:
        return f"{rel}: not committed"
    if _git(root, "status", "--porcelain", "--", rel).stdout.strip():
        return f"{rel}: has uncommitted changes"
    return None


def strategy_problems(strategy: Any, *, check_git: bool = True, check_location: bool = True) -> List[str]:
    from research.shadow.run_day import Journal, ShadowError
    from research.studies import prereg_io

    probs: List[str] = []
    pre = Path(strategy.prereg_path())
    if not pre.exists():
        return [f"{strategy.id}: pre-registration {pre} does not exist"]
    try:
        spec = prereg_io.load(pre)
    except Exception as exc:
        return [f"{strategy.id}: pre-registration unreadable ({type(exc).__name__}: {exc})"]
    if spec.get("status") != strategy.required_status:
        probs.append(f"{strategy.id}: pre-registration status is {spec.get('status')!r}, not "
                     f"{strategy.required_status}")
    if spec.get("id") != strategy.id:
        probs.append(f"{strategy.id}: pre-registration id is {spec.get('id')!r}")
    ev = strategy.evaluation or {}
    ra, fa = ev.get("review_after"), ev.get("futility_after")
    if not (isinstance(ra, int) and isinstance(fa, int) and 1 <= fa <= ra):
        probs.append(f"{strategy.id}: evaluation needs integers 1 <= futility_after <= review_after, got {ev}")
    if not (isinstance(strategy.hold_sessions, int) and strategy.hold_sessions >= 1):
        probs.append(f"{strategy.id}: hold_sessions must be a positive integer")
    if check_git:
        g = prereg_git_problem(pre)
        if g:
            probs.append(f"{strategy.id}: {g}")
    for replay in (False, True):
        jp = strategy.journal_path(replay=replay)
        if check_location:
            loc = journal_location_problem(jp)
            if loc:
                probs.append(f"{strategy.id}: {loc}")
        try:
            rows = Journal(jp).verify()
        except ShadowError as exc:
            probs.append(f"{strategy.id}: {exc}")
            continue
        if not replay:
            shas = {r.get("prereg_sha256") for r in rows if r.get("kind") == "PLAN" and r.get("prereg_sha256")}
            if shas and shas != {prereg_io.normalised_sha256(pre)}:
                probs.append(f"{strategy.id}: PREREG_CHANGED since the first plan (Rule F3)")
    return probs
