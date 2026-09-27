"""
research/data/holdout.py
========================
HoldoutGuard (plan P3.7, P7.1): keeps the pre-registered holdout sessions away from strategy code until
the pre-registration is locked.

Rules
1. STRATEGY mode (the default). Unless research/studies/prereg/<id>.lock is valid (prereg_io.verify_lock),
   every bar dated inside the holdout window - intraday and daily - is invisible: sessions() does not
   list it, bars() and daily*() do not return it. Calibrations for later sessions therefore cannot read
   holdout data either.
2. QA mode serves every session, for data validation only. Every QA read is logged, and a QA store is
   refused by BacktestEngine and PointInTimeView, so its data can never reach strategy code.
3. Stores built by the legacy JSON loader and by ParquetCandleStore both pass through a guard.

The window comes from the pre-registration YAML (data.holdout). The constants below are the plan's
values (P7.1) and a test checks that the committed YAML agrees with them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

from research.studies import prereg_io

DEFAULT_PREREG = prereg_io.PREREG_DIR / "resid_rev_v1.yaml"
PLAN_HOLDOUT = (date(2024, 10, 1), date(2026, 7, 31))

STRATEGY = "STRATEGY"
QA = "QA"


class HoldoutLockedError(PermissionError):
    """Raised when strategy-mode code asks for a holdout session before the pre-registration lock."""


class QAStoreError(PermissionError):
    """Raised when a QA-mode store is handed to strategy code."""


@dataclass
class HoldoutGuard:
    window: Tuple[date, date] = PLAN_HOLDOUT
    unlocked: bool = False
    reason: str = "LOCK_MISSING"
    mode: str = STRATEGY
    qa_log: List[str] = field(default_factory=list)

    @classmethod
    def from_prereg(cls, yaml_path: Path | str = DEFAULT_PREREG, mode: str = STRATEGY) -> "HoldoutGuard":
        if mode not in (STRATEGY, QA):
            raise ValueError(f"mode must be STRATEGY or QA, got {mode!r}")
        window = PLAN_HOLDOUT
        yaml_path = Path(yaml_path)
        if yaml_path.exists():
            spec = prereg_io.load(yaml_path)
            h = (spec.get("data") or {}).get("holdout")
            if not (isinstance(h, list) and len(h) == 2):
                raise ValueError(f"{yaml_path}: data.holdout must be [start, end]")
            window = (date.fromisoformat(str(h[0])), date.fromisoformat(str(h[1])))
        status = prereg_io.verify_lock(yaml_path)
        return cls(window=window, unlocked=status.valid, reason=status.reason, mode=mode)

    def in_holdout(self, day: date) -> bool:
        return self.window[0] <= day <= self.window[1]

    def hidden(self, day: date) -> bool:
        """True when this date must be invisible to the caller."""
        return self.mode == STRATEGY and not self.unlocked and self.in_holdout(day)

    def check(self, symbol: str, day: date) -> None:
        if self.hidden(day):
            raise HoldoutLockedError(f"{symbol} {day}: holdout session, pre-registration not locked ({self.reason})")
        if self.mode == QA:
            self.qa_log.append(f"{symbol} {day}")


def assert_not_qa(store: object, who: str) -> None:
    if getattr(store, "mode", STRATEGY) == QA:
        raise QAStoreError(f"{who}: a QA-mode store cannot feed strategy code")


def default_guard(mode: str = STRATEGY, yaml_path: Optional[Path | str] = None) -> HoldoutGuard:
    return HoldoutGuard.from_prereg(yaml_path or DEFAULT_PREREG, mode=mode)
