"""
research/framework/archive.py
=============================
ArchiveMarket: the NSE 2005-2021 archive (data program JOBs 1-3) behind the same interface as MarketFiles, so any
plug-in and the backtester read it unchanged.

- Files come only from SAVED manifest lines; every lookup and every read re-checks the file's SHA-256 against the
  manifest, and a read parses exactly the bytes it hashed (a mismatch raises ArchiveIntegrityError: fail closed).
- The archive is point-in-time: dead and renamed companies are present as they traded (no survivorship bias).
  A renamed symbol simply stops, so a strategy excludes it or VOIDs the trade; nothing is guessed.
- The archive knows the future, so the entry session is the next session on file. Missing sessions (weekend
  specials, archive gaps) are caught by chain(), which every plan and score already checks.
- F&O ban lists do not exist before 2021-10 here (JOB 5 pending): ban() is None and ban_lists is False, so a
  strategy must state its assumption explicitly (see ExpiryReliefV2.entry_ban).
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from research.framework.market import MarketFiles, parse_bhavcopy

DATASETS = {"cm_bhavcopy": "cm", "fo_bhavcopy": "fo", "mto": "mto"}


class ArchiveIntegrityError(RuntimeError):
    """A file on disk is not the file the manifest recorded."""


class ArchiveMarket(MarketFiles):
    ban_lists = False

    def __init__(self, history: Path) -> None:
        super().__init__(history)
        self._paths: Dict[str, Dict[date, Tuple[Path, str]]] = {k: {} for k in DATASETS.values()}
        self._attempted_cm: Set[date] = set()
        m = self.h / "raw" / "nse_archive" / "manifest.jsonl"
        for ln in m.read_text(encoding="utf-8").splitlines() if m.exists() else []:
            if not ln.strip():
                continue
            r = json.loads(ln)
            kind = DATASETS.get(r.get("dataset"))
            if kind == "cm" and r.get("outcome") in ("SAVED", "MISSING_404") and r.get("trade_date"):
                self._attempted_cm.add(date.fromisoformat(r["trade_date"]))
            if r.get("outcome") != "SAVED" or kind is None or not r.get("saved_path"):
                continue
            self._paths[kind][date.fromisoformat(r["trade_date"])] = (self.h / r["saved_path"], r.get("sha256", ""))
        self._sessions = sorted(self._paths["cm"])
        self._sha_by_path = {p: sha for kind in self._paths.values() for p, sha in kind.values()}

    def _path(self, kind: str, d: date) -> Path:
        """The manifest's file for (kind, d), its SHA-256 re-checked on EVERY lookup (CODEX-FRAMEWORK-001 A6: a file
        changed after its first check must never pass later)."""
        hit = self._paths[kind].get(d)
        if hit is None:
            return self.h / "__not_in_archive__" / kind / d.isoformat()
        p, sha = hit
        if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            raise ArchiveIntegrityError(f"{p}: SHA-256 differs from the manifest (fail closed)")
        return p

    def _read(self, path: Path):
        """Parse exactly the bytes that were checked: read once, hash, then parse those bytes (no check-then-read gap)."""
        raw = Path(path).read_bytes()
        want = self._sha_by_path.get(Path(path))
        if want is None or hashlib.sha256(raw).hexdigest() != want:
            raise ArchiveIntegrityError(f"{path}: not a manifest file, or SHA-256 differs (fail closed)")
        return parse_bhavcopy(raw, str(path))

    def cm_path(self, d: date) -> Path:
        return self._path("cm", d)

    def fo_path(self, d: date) -> Path:
        return self._path("fo", d)

    def ban(self, d: date) -> Optional[Set[str]]:
        return None

    def surveillance_required(self, d: date) -> bool:        # no ASM/GSM history exists for the archive years
        return False

    def surveillance(self, d: date) -> Optional[Set[str]]:
        return None

    def uncovered(self, start: date, end: date) -> List[date]:
        """Weekdays in [start, end] for which the manifest has no CM outcome at all (neither SAVED nor a 404)."""
        out, d = [], start
        while d <= end:
            if d.weekday() < 5 and d not in self._attempted_cm:
                out.append(d)
            d += timedelta(days=1)
        return out

    def entry_session(self, day: date) -> Tuple[Optional[date], List[date]]:
        later = [d for d in self.sessions() if d > day]
        return (later[0], []) if later else (None, [])
