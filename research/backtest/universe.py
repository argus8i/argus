"""
research/backtest/universe.py
=============================
Point-in-time universe filter for Track 2 liquid short-term momentum.
Enforces Rule 2 (Rs 10 floor), F&O membership timeline, and regulatory surveillance isolation (Rule 6).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Sequence, Tuple


@dataclass
class PointInTimeUniverse:
    """
    Point-in-time universe representation.
    Tracks F&O membership intervals and surveillance classification windows.
    Fails closed: symbols outside their active F&O window or under surveillance are ineligible.
    """
    fno_membership: Sequence[Tuple[str, date, Optional[date]]]
    surveillance: Sequence[Tuple[str, str, date, Optional[date]]] = field(default_factory=list)
    min_price: float = 10.0
    assumed: bool = False
    note: str = ""

    def check(self, symbol: str, dt: date, price: Optional[float] = None) -> Tuple[bool, str]:
        sym = symbol.strip().upper()

        # 1. Price floor gate (Rule 2)
        if price is not None and price < self.min_price:
            return False, "PRICE_BELOW_FLOOR"

        # 2. F&O membership check
        is_member = False
        for s, start, end in self.fno_membership:
            if s.upper() == sym:
                if start <= dt and (end is None or dt <= end):
                    is_member = True
                    break
        if not is_member:
            return False, "NOT_FNO_MEMBER"

        # 3. Surveillance check (ASM, GSM, ESM, etc.)
        for s, reason, start, end in self.surveillance:
            if s.upper() == sym:
                if start <= dt and (end is None or dt <= end):
                    return False, f"SURVEILLANCE_{reason.upper()}"

        return True, "ELIGIBLE"

    @classmethod
    def assumed_static(
        cls,
        symbols: Sequence[str],
        start: date,
        end: date,
        note: str = "assumed",
    ) -> PointInTimeUniverse:
        """Helper for backtesting on an assumed static universe with explicit provenance tracking."""
        membership = [(s.strip().upper(), start, end) for s in symbols]
        return cls(fno_membership=membership, surveillance=[], assumed=True, note=note)
