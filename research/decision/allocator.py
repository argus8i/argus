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

RankItem = Tuple[int, str, int, Any]          # (strategy priority, symbol, bar index, SignalIntent)


class Allocator:
    def rank(self, candidates: Sequence[RankItem], session: date) -> List[RankItem]:
        raise NotImplementedError


class DefaultAllocator(Allocator):
    def __init__(self, seed: int = 0) -> None:
        self.seed = seed

    def _tiebreak(self, symbol: str, session: date) -> int:
        digest = hashlib.sha256(f"{self.seed}|{symbol}|{session.isoformat()}".encode()).digest()
        return int.from_bytes(digest[:8], "big")

    def rank(self, candidates: Sequence[RankItem], session: date) -> List[RankItem]:
        return sorted(candidates, key=lambda c: (c[0], -float(getattr(c[3], "priority_score", 0.0) or 0.0),
                                                 self._tiebreak(c[1], session)))


# ================================================================================================ P6.5
# Bar-close allocation for the decision engine (plan P6.5) under Yashu's Adjusted A1 limits (25 Sep 2026):
# 3 slots, Rs 38,000 per position, Rs 1,14,000 aggregate ABSOLUTE notional across active positions plus
# pending entry reservations (long and short are added, never netted). Standalone: the shadow runner (P8)
# and the portfolio simulation call it with every candidate emitted at one bar close plus the book.
# BacktestEngine does NOT call it (the engine still applies its own P2 slot/sector/cluster checks with
# EngineConfig.slot_cap_rs = 58,333.33); see research/notes/p6_report.md.
import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional, Set

from research.decision.sizing import AGGREGATE_EXPOSURE_CAP_RS, MAX_SLOTS, SLOT_CAP_RS

SHADOW, EXPLOIT = "SHADOW", "EXPLOIT"
MAX_PER_CLUSTER, MAX_PER_SECTOR = 1, 2
_EPS = 1e-6


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(float(x))


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
    notional: float = 0.0               # absolute planned entry notional (qty x worst admissible entry price)
    cluster: Optional[str] = None
    sector: Optional[str] = None


@dataclass(frozen=True)
class Position:
    """A booked exposure: an active (filled, still held) position, or a pending entry reservation.
    notional is ABSOLUTE rupees (a short counts like a long). A partially filled order appears once in
    each list (filled part active, unfilled part pending) and occupies one slot."""
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


def _booked_exposure(positions: Sequence[Position], what: str) -> float:
    total = 0.0
    for p in positions:
        if not _is_num(p.notional) or p.notional <= 0:
            raise ValueError(f"{what} {p.symbol!r}: invalid notional {p.notional!r} (fail closed)")
        total += abs(float(p.notional))
    return total


def allocate(candidates: Iterable[Candidate], *, mode: str = SHADOW, open_positions: Sequence[Position] = (),
             pending_orders: Sequence[Position] = (), free_cash: float, promoted: Optional[Set[str]] = None,
             seed: int = 0, max_slots: int = MAX_SLOTS, slot_cap_rs: float = SLOT_CAP_RS,
             aggregate_cap_rs: float = AGGREGATE_EXPOSURE_CAP_RS, max_per_cluster: int = MAX_PER_CLUSTER,
             max_per_sector: int = MAX_PER_SECTOR) -> Allocation:
    """Rules (plan P6.5; Adjusted A1 limits), every drop logged with its reason:
    1. same symbol, opposite sides: drop both unless |dE| >= 2 * SE_diff (SE_diff = sqrt(se1^2 + se2^2),
       finite and > 0); then keep the higher expected_net_r.
    2. same symbol, same side: keep the higher expected_net_r (ties: lower priority number, then strategy id).
    3. ranking. SHADOW: (priority, -priority_score, seeded hash). EXPLOIT: promoted strategies only, drop
       expected_net_r <= 0 or unknown, rank by expected_net_r * risk_rs * m (descending).
    4. greedy fill, counting the booked book (active positions AND pending entry reservations):
       ALREADY_HELD / PENDING_ENTRY, MAX_SLOTS (distinct booked symbols), INVALID_NOTIONAL, SLOT_CAP,
       AGGREGATE_EXPOSURE_CAP (sum of absolute notionals, active + pending + this candidate), NO_FREE_CASH,
       CLUSTER_LIMIT, SECTOR_LIMIT. An unknown cluster or sector is one shared bucket '?' (fail closed).
    Invalid limits, free cash or booked notionals raise ValueError; an invalid candidate is dropped."""
    if mode not in (SHADOW, EXPLOIT):
        raise ValueError(f"mode must be SHADOW or EXPLOIT, got {mode!r}")
    for name, v in (("free_cash", free_cash), ("slot_cap_rs", slot_cap_rs), ("aggregate_cap_rs", aggregate_cap_rs)):
        if not _is_num(v) or v < 0:
            raise ValueError(f"{name} must be a finite non-negative number, got {v!r}")
    if isinstance(max_slots, bool) or not isinstance(max_slots, int) or max_slots < 0:
        raise ValueError(f"max_slots must be a non-negative int, got {max_slots!r}")
    exposure = _booked_exposure(open_positions, "open position") + _booked_exposure(pending_orders, "pending order")

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
    pending = {p.symbol.upper() for p in pending_orders} - held
    booked: Dict[str, Position] = {}
    for p in list(open_positions) + list(pending_orders):
        booked.setdefault(p.symbol.upper(), p)                  # one slot per symbol (partial fill = 1 slot)
    slots = len(booked)
    per_cluster: Dict[str, int] = {}
    per_sector: Dict[str, int] = {}
    for p in booked.values():
        per_cluster[p.cluster or "?"] = per_cluster.get(p.cluster or "?", 0) + 1
        per_sector[p.sector or "?"] = per_sector.get(p.sector or "?", 0) + 1
    cash = float(free_cash)
    for c in ranked:
        cl, se_ = c.cluster or "?", c.sector or "?"
        sym = c.symbol.upper()
        if sym in held:
            reason = "ALREADY_HELD"
        elif sym in pending:
            reason = "PENDING_ENTRY"
        elif slots >= max_slots:
            reason = "MAX_SLOTS"
        elif not _is_num(c.notional) or c.notional <= 0:
            reason = "INVALID_NOTIONAL"
        elif c.notional > slot_cap_rs + _EPS:
            reason = "SLOT_CAP"
        elif exposure + abs(c.notional) > aggregate_cap_rs + _EPS:
            reason = "AGGREGATE_EXPOSURE_CAP"
        elif c.notional > cash + _EPS:
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
        pending.add(sym)                      # an allocated candidate becomes a pending reservation
        slots += 1
        exposure += abs(c.notional)
        cash -= c.notional
        per_cluster[cl] = per_cluster.get(cl, 0) + 1
        per_sector[se_] = per_sector.get(se_, 0) + 1
    return out


# ------------------------------------------------------------------------------------------ exposure book
class CapacityError(ValueError):
    """A reservation would break a slot, slot-cap or aggregate-exposure limit, or its inputs are invalid."""


@dataclass
class _Order:
    order_id: str
    symbol: str
    side: str
    qty: int
    reserve_px: float                    # worst admissible entry price used for the reservation
    cluster: Optional[str]
    sector: Optional[str]
    filled_qty: int = 0
    filled_notional: float = 0.0
    closed_qty: int = 0
    cancelled: bool = False

    @property
    def remaining_qty(self) -> int:
        return 0 if self.cancelled else self.qty - self.filled_qty

    @property
    def open_qty(self) -> int:
        return self.filled_qty - self.closed_qty

    @property
    def pending_notional(self) -> float:
        return self.remaining_qty * self.reserve_px

    @property
    def active_notional(self) -> float:
        """Entry notional of the shares still held (average fill price x open quantity)."""
        return self.filled_notional * self.open_qty / self.filled_qty if self.filled_qty else 0.0

    @property
    def live(self) -> bool:
        return self.remaining_qty > 0 or self.open_qty > 0


class ExposureBook:
    """Active positions plus pending entry reservations, in absolute notional (Adjusted A1).

    reserve(): books qty x reserve_px as pending; refused (CapacityError) unless the symbol is free, a slot
               is free, notional <= slot cap and booked exposure + notional <= aggregate cap.
    fill():    a partial or full fill moves filled_qty x reserve_px out of pending and filled_qty x fill_px
               into active. The unfilled remainder STAYS reserved; nothing is released early.
    cancel():  releases only the unfilled remainder. The slot is freed only if nothing was ever filled.
    close():   releases active exposure pro rata as shares are sold/covered; the slot is freed at zero.
    A fill worse than the reservation price can push exposure above the cap (a fill is a fact, so it is not
    refused); it is recorded in `breaches` and blocks every new reservation until exposure is back under."""

    def __init__(self, max_slots: int = MAX_SLOTS, slot_cap_rs: float = SLOT_CAP_RS,
                 aggregate_cap_rs: float = AGGREGATE_EXPOSURE_CAP_RS) -> None:
        self.max_slots, self.slot_cap_rs, self.aggregate_cap_rs = max_slots, slot_cap_rs, aggregate_cap_rs
        self.orders: Dict[str, _Order] = {}
        self.breaches: List[Tuple[str, float]] = []

    # -------------------------------------------------------------- views
    def pending_exposure(self) -> float:
        return sum(o.pending_notional for o in self.orders.values())

    def active_exposure(self) -> float:
        return sum(o.active_notional for o in self.orders.values())

    def exposure(self) -> float:
        return self.pending_exposure() + self.active_exposure()

    def slots_used(self) -> int:
        return sum(1 for o in self.orders.values() if o.live)

    def positions(self) -> Tuple[List[Position], List[Position]]:
        """(open_positions, pending_orders) in the form allocate() takes."""
        act = [Position(o.symbol, o.active_notional, o.cluster, o.sector)
               for o in self.orders.values() if o.open_qty > 0]
        pen = [Position(o.symbol, o.pending_notional, o.cluster, o.sector)
               for o in self.orders.values() if o.remaining_qty > 0]
        return act, pen

    # -------------------------------------------------------------- mutations
    def _get(self, order_id: str) -> _Order:
        if order_id not in self.orders:
            raise KeyError(f"unknown order {order_id!r}")
        return self.orders[order_id]

    def reserve(self, order_id: str, symbol: str, side: str, qty: int, reserve_px: float,
                cluster: Optional[str] = None, sector: Optional[str] = None) -> None:
        if order_id in self.orders:
            raise CapacityError(f"duplicate order id {order_id!r}")
        if not isinstance(side, str) or side.upper() not in ("BUY", "SELL"):
            raise CapacityError(f"side must be BUY or SELL, got {side!r}")
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0:
            raise CapacityError(f"qty must be a positive int, got {qty!r}")
        if not _is_num(reserve_px) or reserve_px <= 0:
            raise CapacityError(f"reserve_px must be a positive finite number, got {reserve_px!r}")
        sym = symbol.upper()
        if any(o.live and o.symbol == sym for o in self.orders.values()):
            raise CapacityError(f"{sym}: already booked")
        notional = qty * float(reserve_px)
        if self.slots_used() >= self.max_slots:
            raise CapacityError("MAX_SLOTS")
        if notional > self.slot_cap_rs + _EPS:
            raise CapacityError(f"SLOT_CAP: {notional:.2f} > {self.slot_cap_rs:.2f}")
        if self.exposure() + notional > self.aggregate_cap_rs + _EPS:
            raise CapacityError(f"AGGREGATE_EXPOSURE_CAP: {self.exposure():.2f} + {notional:.2f} > "
                                f"{self.aggregate_cap_rs:.2f}")
        self.orders[order_id] = _Order(order_id, sym, side.upper(), qty, float(reserve_px), cluster, sector)

    def fill(self, order_id: str, qty: int, price: float) -> None:
        o = self._get(order_id)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0 or qty > o.remaining_qty:
            raise ValueError(f"{order_id}: fill qty {qty!r} not in 1..{o.remaining_qty}")
        if not _is_num(price) or price <= 0:
            raise ValueError(f"{order_id}: invalid fill price {price!r}")
        o.filled_qty += qty
        o.filled_notional += qty * float(price)
        if self.exposure() > self.aggregate_cap_rs + _EPS:
            self.breaches.append((order_id, self.exposure()))

    def cancel(self, order_id: str) -> float:
        """Cancel the unfilled remainder; returns the released reservation."""
        o = self._get(order_id)
        released = o.pending_notional
        o.cancelled = True
        return released

    def close(self, order_id: str, qty: int) -> float:
        """Exit qty held shares; returns the released active exposure."""
        o = self._get(order_id)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty <= 0 or qty > o.open_qty:
            raise ValueError(f"{order_id}: close qty {qty!r} not in 1..{o.open_qty}")
        before = o.active_notional
        o.closed_qty += qty
        return before - o.active_notional
