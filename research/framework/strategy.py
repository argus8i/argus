"""
research/framework/strategy.py
==============================
The plug-in shape every paper strategy fills in. A strategy supplies its rules; the desk (desk.py) supplies
everything that must be the same for all strategies: the journal, the 09:00 prospective rule, duplicates,
pre-registration and code checks, data-gap checks, scoring once, and the evaluation.

A strategy must:
  - point at its pre-registration (research/studies/prereg/<file>.yaml, status LOCKED_PROSPECTIVE);
  - keep its journal under shared/track2_liquid/paper/<folder>/ (Track 2; AGENTS.md Rule 11);
  - say which days are plan days, build a plan for a day, and score one signal over its hold sessions;
  - contain no broker code (AGENTS.md Rule 1; enforced by rules.paper_only_scan in the tests).

build_plan returns {"status": "OK", "signals": [{"symbol", "side", ...}], "book": [...], "data_files": {...}} or
{"status": <anything else>, "reason": ...}. score returns {"exit_reason", "net_r", ...}; net_r None means VOID
(recorded and counted, never silently dropped).
"""
from __future__ import annotations

from datetime import date, time
from pathlib import Path
from typing import Any, Dict, List, Optional


class PaperStrategy:
    id: str = ""
    hold_sessions: int = 0
    entry_deadline: time = time(9, 0)
    required_status: str = "LOCKED_PROSPECTIVE"
    evaluation: Dict[str, Any] = {}            # review_after, futility_after, t_pass (from the pre-registration)

    def prereg_path(self) -> Path:
        raise NotImplementedError

    def journal_path(self, replay: bool = False) -> Path:
        raise NotImplementedError

    def is_plan_day(self, md: Any, day: date) -> bool:
        raise NotImplementedError

    def build_plan(self, md: Any, day: date, entry: date) -> Dict[str, Any]:
        raise NotImplementedError

    def score(self, md: Any, plan_row: Dict[str, Any], signal: Dict[str, Any], hold: List[date]) -> Dict[str, Any]:
        raise NotImplementedError


def paper_root() -> Path:
    from research.data import paths

    return paths.main_checkout() / "shared" / "track2_liquid" / "paper"


def history_root() -> Path:
    from research.data import paths

    return paths.history_dir()


def registered() -> List[PaperStrategy]:
    """Every strategy the daily run paper-trades. Adding one here is a reviewed code change (Rule 8)."""
    from research.shadow.expiry_desk import ExpiryReliefV2

    return [ExpiryReliefV2()]


def by_id(strategy_id: str, strategies: Optional[List[PaperStrategy]] = None) -> PaperStrategy:
    for s in strategies if strategies is not None else registered():
        if s.id == strategy_id:
            return s
    raise KeyError(strategy_id)
