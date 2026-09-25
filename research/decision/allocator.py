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


# ================================================================================================ P6.5
# Bar-close allocation for the decision engine (plan P6.5). Standalone: the shadow runner (P8) and the
# portfolio simulation call it with every candidate emitted at one bar close plus the open book.
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Set

SHADOW, EXPLOIT = "SHADOW", "EXPLOIT"
MAX_SLOTS, SLOT_CAP_RS, MAX_PER_CLUSTER, MAX_PER_SECTOR = 3, 58333.33, 1, 2


@dataclass(frozen=True)
class Candidate:
    strategy_id: str
    symbol: str
    side: str
    session: date
    priority: int                       # pre-registered strategy priority (lower first)
    priority_score: float = 0.0         # strategy's own ranking score (e.g. |Z| for RESID_REV)
    expected_net_r: float = math.nan    # estimator.expected_net_r (NaN when unknown)
    se: float = math.nan                # SE of the strategy estimate (for the conflict rule)
    risk_rs: float = 0.0
    m: float = 1.0                      # sizing.vol_target_multiplier
    notional: float = 0.0
    cluster: Optional[str] = None
    sector: Optional[str] = None


@dataclass(frozen=True)
class Position:
    symbol: str
    notional: float
    cluster: Optional[str] = None
    sector: Optional[str] = None


@dataclass
class Allocation:
    allocated: List[Candidate] = field(default_factory=list)
    dropped: List[Tuple[Candidate, str]] = field(default_factory=list)


def _hash(seed: int, symbol: str, session: date) -> int:
    return int.from_bytes(hashlib.sha256(f"{seed}|{symbol}|{session.isoformat()}".encode()).digest()[:8], "big")


def _e(c: Candidate) -> float:
    return c.expected_net_r if math.isfinite(c.expected_net_r) else -math.inf


def allocate(candidates: Iterable[Candidate], *, mode: str = SHADOW, open_positions: Sequence[Position] = (),
             free_cash: float, promoted: Optional[Set[str]] = None, seed: int = 0,
             max_slots: int = MAX_SLOTS, slot_cap_rs: float = SLOT_CAP_RS,
             max_per_cluster: int = MAX_PER_CLUSTER, max_per_sector: int = MAX_PER_SECTOR) -> Allocation:
    """Rules (plan P6.5), every drop logged with its reason:
    1. same symbol, opposite sides: drop both unless |dE| >= 2 * SE_diff (SE_diff = sqrt(se1^2 + se2^2),
       finite and > 0); then keep the higher expected_net_r.
    2. same symbol, same side: keep the higher expected_net_r (ties: lower priority number, then strategy id).
    3. ranking. SHADOW: (priority, -priority_score, seeded hash). EXPLOIT: promoted strategies only, drop
       expected_net_r <= 0 or unknown, rank by expected_net_r * risk_rs * m (descending).
    4. greedy fill: <= max_slots positions, notional <= slot cap each, total new notional <= free cash,
       <= max_per_cluster per cluster and <= max_per_sector per sector, counting the open positions.
    An unknown cluster or sector is its own shared bucket ('?'), so it is capped too (fail closed)."""
    if mode not in (SHADOW, EXPLOIT):
        raise ValueError(f"mode must be SHADOW or EXPLOIT, got {mode!r}")
    out = Allocation()
    by_symbol: Dict[str, List[Candidate]] = {}
    for c in candidates:
        by_symbol.setdefault(c.symbol.upper(), []).append(c)

    survivors: List[Candidate] = []
    for sym, group in by_symbol.items():
        best_by_side: Dict[str, Candidate] = {}
        for side in ("BUY", "SELL"):
            same = sorted((c for c in group if c.side.upper() == side),
                          key=lambda c: (-_e(c), c.priority, c.strategy_id))
            if same:
                best_by_side[side] = same[0]
                out.dropped += [(c, "DUPLICATE_SAME_SIDE_LOWER_E") for c in same[1:]]
        if len(best_by_side) == 2:
            b, s = best_by_side["BUY"], best_by_side["SELL"]
            se_diff = math.sqrt(b.se ** 2 + s.se ** 2) if math.isfinite(b.se) and math.isfinite(s.se) else math.nan
            d = abs(_e(b) - _e(s)) if math.isfinite(b.expected_net_r) and math.isfinite(s.expected_net_r) else math.nan
            if math.isfinite(se_diff) and se_diff > 0 and math.isfinite(d) and d >= 2 * se_diff:
                keep, lose = (b, s) if _e(b) > _e(s) else (s, b)
                survivors.append(keep)
                out.dropped.append((lose, "CONFLICT_OPPOSITE_SIDE_LOWER_E"))
            else:
                out.dropped += [(b, "CONFLICT_OPPOSITE_SIDES"), (s, "CONFLICT_OPPOSITE_SIDES")]
        else:
            survivors += list(best_by_side.values())

    if mode == EXPLOIT:
        ranked = []
        for c in survivors:
            if promoted is None or c.strategy_id not in promoted:
                out.dropped.append((c, "NOT_PROMOTED"))
            elif not (math.isfinite(c.expected_net_r) and c.expected_net_r > 0):
                out.dropped.append((c, "NON_POSITIVE_EXPECTED_R"))
            else:
                ranked.append(c)
        ranked.sort(key=lambda c: (-(c.expected_net_r * c.risk_rs * c.m), _hash(seed, c.symbol, c.session)))
    else:
        ranked = sorted(survivors, key=lambda c: (c.priority, -float(c.priority_score or 0.0),
                                                  _hash(seed, c.symbol, c.session)))

    held = {p.symbol.upper() for p in open_positions}
    slots = len(open_positions)
    per_cluster: Dict[str, int] = {}
    per_sector: Dict[str, int] = {}
    for p in open_positions:
        per_cluster[p.cluster or "?"] = per_cluster.get(p.cluster or "?", 0) + 1
        per_sector[p.sector or "?"] = per_sector.get(p.sector or "?", 0) + 1
    cash = free_cash
    for c in ranked:
        cl, se_ = c.cluster or "?", c.sector or "?"
        if c.symbol.upper() in held:
            reason = "ALREADY_HELD"
        elif slots >= max_slots:
            reason = "MAX_SLOTS"
        elif not (c.notional > 0) or c.notional > slot_cap_rs + 1e-6:
            reason = "SLOT_CAP"
        elif c.notional > cash + 1e-6:
            reason = "NO_FREE_CASH"
        elif per_cluster.get(cl, 0) >= max_per_cluster:
            reason = "CLUSTER_LIMIT"
        elif per_sector.get(se_, 0) >= max_per_sector:
            reason = "SECTOR_LIMIT"
        else:
            reason = ""
        if reason:
            out.dropped.append((c, reason))
            continue
        out.allocated.append(c)
        held.add(c.symbol.upper())
        slots += 1
        cash -= c.notional
        per_cluster[cl] = per_cluster.get(cl, 0) + 1
        per_sector[se_] = per_sector.get(se_, 0) + 1
    return out
