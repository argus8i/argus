"""
research/decision/allocator.py
==============================
Allocator interface (plan D6). The engine collects every intent emitted at a bar close and asks the
allocator for the order in which to try them; the engine then applies the slot, sector and cluster caps.

DefaultAllocator ranks by (strategy priority, higher priority_score first, seeded hash of symbol and
session). Nothing depends on alphabetical order. P6 replaces the ranking with expected rupee P&L for
promoted strategies (plan P6.5).
"""
from __future__ import annotations

import hashlib
from datetime import date
from typing import Any, List, Sequence, Tuple

Candidate = Tuple[int, str, int, Any]          # (strategy priority, symbol, bar index, SignalIntent)


class Allocator:
    def rank(self, candidates: Sequence[Candidate], session: date) -> List[Candidate]:
        raise NotImplementedError


class DefaultAllocator(Allocator):
    def __init__(self, seed: int = 0) -> None:
        self.seed = seed

    def _tiebreak(self, symbol: str, session: date) -> int:
        digest = hashlib.sha256(f"{self.seed}|{symbol}|{session.isoformat()}".encode()).digest()
        return int.from_bytes(digest[:8], "big")

    def rank(self, candidates: Sequence[Candidate], session: date) -> List[Candidate]:
        return sorted(candidates, key=lambda c: (c[0], -float(getattr(c[3], "priority_score", 0.0) or 0.0),
                                                 self._tiebreak(c[1], session)))
