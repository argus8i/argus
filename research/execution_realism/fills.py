"""Pillar 1: fill models that replace the price-touch fallacy (R06).

Rules under NSE price-time priority, for an order that was live at the exchange:
  * a TOUCH (trade at your price) proves nothing about your fill;
  * a TRADE-THROUGH (a print beyond your price) proves the fill;
  * a QUOTE-THROUGH (opposite best at or beyond your price) proves the fill;
  * otherwise you fill only after volume at your price exhausts the queue ahead.

Three envelopes run in lockstep:
  PESSIMISTIC  only volume that is certain to have traded at your price, no
               cancellation credit. This is the accounting envelope: only its
               fills may advance inventory or P&L.
  CENTRAL      estimated volume and cancellations (uncalibrated priors unless
               QueueParams.calibrated is set by a fitted study).
  OPTIMISTIC   every ambiguous print and every cancellation in your favour.
If a result changes sign between PESSIMISTIC and OPTIMISTIC, it depends on the
fill model and is not evidence.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from .marketdata import (EPS, FeedInvalid, Session, Snapshot, VolumeDelta,
                         on_grid, price_to_ticks)


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class FillState(str, Enum):
    LOCKED_NO_BID = "LOCKED_NO_BID"        # our SELL, and no bids exist at all
    LOCKED_NO_OFFER = "LOCKED_NO_OFFER"    # our BUY, and no offers exist at all
    QUEUED = "QUEUED"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    PURGED = "PURGED"                      # exchange purged unmatched orders (MWCB halt)
    CANCELLED = "CANCELLED"


class Envelope(str, Enum):
    PESSIMISTIC = "PESSIMISTIC"
    CENTRAL = "CENTRAL"
    OPTIMISTIC = "OPTIMISTIC"


PESS, CENT, OPT = Envelope.PESSIMISTIC, Envelope.CENTRAL, Envelope.OPTIMISTIC


class Evidence(str, Enum):
    TRADE_THROUGH = "TRADE_THROUGH"
    QUOTE_THROUGH = "QUOTE_THROUGH"
    QUEUE_CERTAIN = "QUEUE_CERTAIN"
    QUEUE_ESTIMATED = "QUEUE_ESTIMATED"
    BOOK_WALK = "BOOK_WALK"
    AUCTION = "AUCTION"


@dataclass(frozen=True)
class FillEvent:
    envelope: Envelope
    qty: int
    price: float
    evidence: Evidence
    seq: int
    ts: float


@dataclass(frozen=True)
class QueueParams:
    """Uncalibrated engineering priors unless `calibrated` is True.

    alpha                share of an interval's volume credited to our level when the
                         last print was at our price and depletion shares are uninformative
    cancel_power         n in the hftbacktest allocation p_back = B^n / (B^n + F^n)
    first_visible_ahead  CENTRAL fraction of displayed quantity assumed ahead of us when a
                         level we rest at first enters the visible depth (PESS=1, OPT=0)
    """
    alpha: float = 0.5
    cancel_power: float = 2.0
    first_visible_ahead: float = 0.5
    calibrated: bool = False
    source: str = "UNCALIBRATED_PRIOR"

    def __post_init__(self):
        for name in ("alpha", "first_visible_ahead"):
            v = getattr(self, name)
            if not (isinstance(v, (int, float)) and math.isfinite(v) and 0.0 <= v <= 1.0):
                raise ValueError(f"{name} must be in [0, 1]")
        if not (math.isfinite(self.cancel_power) and self.cancel_power > 0):
            raise ValueError("cancel_power must be positive")


def _cancel_share_ahead(front: float, back: float, n: float) -> float:
    """Probability that a unit of unexplained depletion came from AHEAD of us."""
    if front <= 0:
        return 0.0
    if back <= 0:
        return 1.0
    fb, ff = back ** n, front ** n
    return ff / (ff + fb)


class PassiveOrderSim:
    """A resting limit order at `price` (not marketable when it joins)."""

    def __init__(self, order_id: str, side: Side, price: float, qty: int, tick: float,
                 params: QueueParams = QueueParams()):
        if not (isinstance(qty, int) and not isinstance(qty, bool) and qty > 0):
            raise ValueError("qty must be a positive int")
        if not on_grid(price, tick):
            raise ValueError("price off the tick grid")
        self.order_id, self.side, self.price, self.qty, self.tick = order_id, Side(side), float(price), qty, float(tick)
        self.params = params
        self._same = "BID" if self.side is Side.BUY else "ASK"
        self._opp = "ASK" if self.side is Side.BUY else "BID"
        self.filled: Dict[Envelope, int] = {e: 0 for e in Envelope}
        self.notional: Dict[Envelope, float] = {e: 0.0 for e in Envelope}
        self.rank: Dict[Envelope, Optional[float]] = {e: None for e in Envelope}
        self.state: Dict[Envelope, FillState] = {e: FillState.QUEUED for e in Envelope}
        self.events: List[FillEvent] = []
        self.prev: Optional[Snapshot] = None
        self.terminal: Optional[FillState] = None
        self.frozen_intervals = 0
        self._cancel_effective_at: Optional[float] = None

    # -- geometry -----------------------------------------------------------
    def _crosses(self, opp_price: float) -> bool:
        return self.price >= opp_price - EPS if self.side is Side.BUY else self.price <= opp_price + EPS

    def _improves(self, same_best: float) -> bool:
        return self.price > same_best + EPS if self.side is Side.BUY else self.price < same_best - EPS

    def _opp_best(self, s: Snapshot) -> Optional[float]:
        return s.best_ask if self.side is Side.BUY else s.best_bid

    def _same_best(self, s: Snapshot) -> Optional[float]:
        return s.best_bid if self.side is Side.BUY else s.best_ask

    # -- lifecycle ------------------------------------------------------------
    def join(self, s: Snapshot) -> None:
        """Call with the first validated snapshot at/after the order reached the exchange."""
        if self.prev is not None:
            raise RuntimeError("already joined")
        if s.session is not Session.CONTINUOUS:
            raise FeedInvalid("cannot join a continuous-session queue outside the continuous session")
        opp = self._opp_best(s)
        if opp is not None and self._crosses(opp):
            raise ValueError("order is marketable against this book: use taker_fill first")
        same = self._same_best(s)
        if same is None or self._improves(same):
            ahead: Optional[float] = 0.0
        else:
            q = s.qty_at(self._same, self.price, self.tick)
            ahead = None if q is None else float(q)       # join at the back of the level
        for e in Envelope:
            self.rank[e] = ahead
        self.prev = s
        self._update_states(s)

    def request_cancel(self, t_request: float, ack_latency_s: float) -> None:
        """Fills evidenced in the interval containing the cancel's arrival still count."""
        self._cancel_effective_at = t_request + max(0.0, ack_latency_s)

    def on_snapshot(self, s: Snapshot, vd: VolumeDelta) -> None:
        if self.terminal is not None:
            return
        p = self.prev
        if p is None:
            raise RuntimeError("join() first")
        if vd.symbol != s.symbol or abs(vd.t1 - s.recv_ts) > 1e-9:
            raise ValueError("volume delta does not belong to this snapshot")
        if s.session is Session.HALTED:
            self._terminate(FillState.PURGED, s)
            return
        if s.session is not Session.CONTINUOUS:
            self.prev = s
            return
        trade_in_interval = ((vd.delta is not None and vd.delta > 0) or
                             (s.ltt is not None and p.ltt is not None and s.ltt > p.ltt + EPS))
        through = self._through_evidence(p, s, trade_in_interval)
        if through is not None:
            for e in Envelope:
                self._fill(e, self.qty - self.filled[e], through, s)
        else:
            self._queue_step(p, s, vd, trade_in_interval)
        self.prev = s
        self._update_states(s)
        if self._cancel_effective_at is not None and s.recv_ts >= self._cancel_effective_at:
            self._terminate(FillState.CANCELLED, s)

    # -- evidence -------------------------------------------------------------
    def _through_evidence(self, p: Snapshot, s: Snapshot, trade_in_interval: bool) -> Optional[Evidence]:
        P = self.price
        if self.side is Side.BUY:
            if s.day_low < p.day_low - EPS and s.day_low < P - EPS:
                return Evidence.TRADE_THROUGH          # a new low printed below our bid
            if trade_in_interval and s.ltp < P - EPS:
                return Evidence.TRADE_THROUGH
            if s.best_ask is not None and s.best_ask <= P + EPS:
                return Evidence.QUOTE_THROUGH
        else:
            if s.day_high > p.day_high + EPS and s.day_high > P + EPS:
                return Evidence.TRADE_THROUGH
            if trade_in_interval and s.ltp > P + EPS:
                return Evidence.TRADE_THROUGH
            if s.best_bid is not None and s.best_bid >= P - EPS:
                return Evidence.QUOTE_THROUGH
        return None

    def _volume_at_price(self, p: Snapshot, s: Snapshot, vd: VolumeDelta, trade_in_interval: bool,
                         q_prev: int, q_now: int) -> Tuple[float, float, float]:
        """(certain, central, optimistic) volume that traded against OUR side at our price."""
        P = self.price
        last_at_price = trade_in_interval and abs(s.ltp - P) < self.tick / 2
        opp_p, opp_s = self._opp_best(p), self._opp_best(s)
        opp_beyond = (opp_p is None or not self._crosses(opp_p)) and (opp_s is None or not self._crosses(opp_s))
        certain = 0.0
        if last_at_price and opp_beyond and s.ltq:
            # The last print was at our price while the opposite quote never reached it:
            # it consumed the front of our level. Its size is a certain lower bound.
            certain = float(s.ltq if vd.delta is None else min(s.ltq, vd.delta))
        if vd.delta is None:
            return certain, certain, certain
        delta = float(vd.delta)
        d_same = max(0, q_prev - q_now)
        d_opp = 0
        if opp_p is not None and opp_s is not None and abs(opp_p - opp_s) < self.tick / 2:
            qp = p.qty_at(self._opp, opp_p, self.tick) or 0
            qs = s.qty_at(self._opp, opp_s, self.tick) or 0
            d_opp = max(0, qp - qs)
        if d_same + d_opp > 0:
            w = d_same / (d_same + d_opp)
            if not last_at_price:
                w *= self.params.alpha
        else:
            w = self.params.alpha if last_at_price else 0.0
        central = min(delta, max(certain, w * delta))
        optimistic = delta if (last_at_price or d_same > 0) else certain
        return certain, central, max(optimistic, central)

    def _queue_step(self, p: Snapshot, s: Snapshot, vd: VolumeDelta, trade_in_interval: bool) -> None:
        q_prev = p.qty_at(self._same, self.price, self.tick)
        q_now = s.qty_at(self._same, self.price, self.tick)
        if q_now is None:                        # level beyond visible depth: freeze
            self.frozen_intervals += 1
            return
        if self.rank[PESS] is None:              # level just became visible
            self.rank[PESS] = float(q_now)
            self.rank[CENT] = self.params.first_visible_ahead * q_now
            self.rank[OPT] = 0.0
            return
        if q_prev is None:                       # re-entered view: only the certain bound applies
            for e in Envelope:
                self.rank[e] = min(self.rank[e], float(q_now))
            return
        v_c, v_m, v_o = self._volume_at_price(p, s, vd, trade_in_interval, q_prev, q_now)
        for e, v in ((PESS, v_c), (CENT, v_m), (OPT, v_o)):
            ev = Evidence.QUEUE_CERTAIN if e is PESS else Evidence.QUEUE_ESTIMATED
            front = self.rank[e]
            if v > front:                        # trades first: the front is consumed, then us
                self._fill(e, min(self.qty - self.filled[e], int(math.floor(v - front + 1e-9))), ev, s)
            front_after = max(0.0, front - v)
            if e is not PESS:                    # cancellations: never credited to PESSIMISTIC
                c = max(0.0, q_prev - q_now - v)
                back_after = max(0.0, (q_prev - v) - front_after)
                if e is OPT:
                    front_after = max(0.0, front_after - c)
                else:
                    share = _cancel_share_ahead(front_after, back_after, self.params.cancel_power)
                    over = max(0.0, (1 - share) * c - back_after)   # cancels beyond the back come from the front
                    front_after = max(0.0, front_after - share * c - over)
            self.rank[e] = min(front_after, float(q_now))           # certain bound: ahead <= displayed

    # -- bookkeeping ----------------------------------------------------------
    def _fill(self, e: Envelope, qty: int, ev: Evidence, s: Snapshot) -> None:
        if qty <= 0:
            return
        qty = min(qty, self.qty - self.filled[e])
        if qty <= 0:
            return
        self.filled[e] += qty
        self.notional[e] += qty * self.price
        self.rank[e] = 0.0
        self.events.append(FillEvent(e, qty, self.price, ev, s.seq, s.recv_ts))

    def _update_states(self, s: Snapshot) -> None:
        opp_empty = not (s.asks if self.side is Side.BUY else s.bids)
        for e in Envelope:
            f = self.filled[e]
            if f >= self.qty:
                st = FillState.FILLED
            elif f > 0:
                st = FillState.PARTIAL
            elif opp_empty:
                st = FillState.LOCKED_NO_OFFER if self.side is Side.BUY else FillState.LOCKED_NO_BID
            else:
                st = FillState.QUEUED
            self.state[e] = st

    def _terminate(self, st: FillState, s: Snapshot) -> None:
        self.terminal = st
        for e in Envelope:
            if self.state[e] is not FillState.FILLED:
                self.state[e] = st
        self.prev = s

    def avg_price(self, e: Envelope = PESS) -> Optional[float]:
        return self.notional[e] / self.filled[e] if self.filled[e] else None


# --------------------------------------------------------------- taker fills
DEFAULT_HAIRCUTS: Tuple[float, ...] = (0.5, 0.3, 0.3, 0.3, 0.3)
"""Fraction of displayed depth assumed still present when a marketable order arrives.
Engineering priors, NOT measurements. Calibrate per level and time bucket as
depth(t + latency) / depth(t) on your own recorded snapshots."""


class SelfConsumption:
    """Depth this simulated trader already took. Stops repeated IOCs re-using the
    same displayed liquidity in consecutive snapshots (our paper order never
    removed it from the real book)."""

    def __init__(self, refractory_s: float = 5.0):
        self.refractory_s = refractory_s
        self._taken: Dict[Tuple[str, str, int], List[Tuple[float, int]]] = {}

    def taken(self, symbol: str, side: str, pt: int, now: float) -> int:
        rows = [(t, q) for t, q in self._taken.get((symbol, side, pt), []) if now - t < self.refractory_s]
        self._taken[(symbol, side, pt)] = rows
        return sum(q for _, q in rows)

    def record(self, symbol: str, side: str, pt: int, qty: int, now: float) -> None:
        self._taken.setdefault((symbol, side, pt), []).append((now, qty))


@dataclass
class TakerResult:
    status: Dict[Envelope, str]
    filled: Dict[Envelope, int]
    vwap: Dict[Envelope, Optional[float]]
    events: List[FillEvent] = field(default_factory=list)


def taker_fill(side: Side, qty: int, limit: float, tick: float, before: Snapshot, after: Snapshot,
               arrival_ts: float, *, haircuts: Sequence[float] = DEFAULT_HAIRCUTS,
               memory: Optional[Dict[Envelope, SelfConsumption]] = None) -> TakerResult:
    """A marketable limit (IOC semantics) arriving at `arrival_ts`, bracketed by two snapshots.

    CENTRAL      haircut depth of the first book at/after arrival.
    PESSIMISTIC  the same, but capped by the haircut depth the earlier book showed at that
                 price; a price the earlier book showed as EMPTY gets nothing (the liquidity
                 may have arrived after us); a price beyond the earlier book's visible depth
                 falls back to the later book (the market moved: it is the only evidence).
    OPTIMISTIC   the larger unhaircut depth of the two books.
    Status per envelope: FILLED | PARTIAL | NO_LIQUIDITY_WITHIN_LIMIT | LOCKED.
    """
    side = Side(side)
    if before.symbol != after.symbol:
        raise ValueError("bracketing snapshots are for different symbols")
    if not (before.recv_ts <= arrival_ts <= after.recv_ts):
        raise FeedInvalid("arrival is not bracketed by the two snapshots")
    if before.session is not Session.CONTINUOUS or after.session is not Session.CONTINUOUS:
        raise FeedInvalid("taker fills are only simulated in the continuous session")
    if not (isinstance(qty, int) and qty > 0) or not on_grid(limit, tick):
        raise ValueError("invalid qty or off-grid limit")
    book_side = "ASK" if side is Side.BUY else "BID"

    def depth(s: Snapshot, full: bool, e: Envelope) -> Dict[int, int]:
        out: Dict[int, int] = {}
        for k, lv in enumerate(s.side_levels(book_side)):
            if (side is Side.BUY and lv.price > limit + EPS) or (side is Side.SELL and lv.price < limit - EPS):
                break
            h = 1.0 if full else haircuts[min(k, len(haircuts) - 1)]
            pt = price_to_ticks(lv.price, tick)
            used = memory[e].taken(s.symbol, book_side, pt, arrival_ts) if memory else 0
            out[pt] = max(0, int(math.floor(h * lv.qty)) - used)
        return out

    res = TakerResult(status={}, filled={}, vwap={})
    for e in Envelope:
        if e is PESS:
            a, b = depth(after, False, e), depth(before, False, e)
            avail = {}
            for pt, v in a.items():
                if pt in b:
                    avail[pt] = min(v, b[pt])
                else:
                    seen = before.qty_at(book_side, round(pt * tick, 10), tick)
                    avail[pt] = v if seen is None else 0
        elif e is CENT:
            avail = depth(after, False, e)
        else:
            a, b = depth(after, True, e), depth(before, True, e)
            avail = {pt: max(a.get(pt, 0), b.get(pt, 0)) for pt in set(a) | set(b)}
        order = sorted(avail) if side is Side.BUY else sorted(avail, reverse=True)
        rem, notional = qty, 0.0
        for pt in order:
            x = min(rem, avail[pt])
            if x <= 0:
                continue
            px = round(pt * tick, 10)
            rem -= x
            notional += x * px
            res.events.append(FillEvent(e, x, px, Evidence.BOOK_WALK, after.seq, arrival_ts))
            if memory:
                memory[e].record(after.symbol, book_side, pt, x, arrival_ts)
            if rem == 0:
                break
        got = qty - rem
        res.filled[e] = got
        res.vwap[e] = notional / got if got else None
        if not after.side_levels(book_side):
            res.status[e] = "LOCKED"
        elif got == qty:
            res.status[e] = "FILLED"
        elif got > 0:
            res.status[e] = "PARTIAL"
        else:
            res.status[e] = "NO_LIQUIDITY_WITHIN_LIMIT"
    return res


# ------------------------------------------------------------ bar replays
def resolve_bar_exit(high: float, low: float, stop_trigger: float, stop_limit: float,
                     target: float, tick: float) -> Dict[str, str]:
    """Bar-level replay for a long with an SL-limit and a take-profit limit.

    A bar cannot order events inside itself, and a touch is not a fill, so the
    result is a pair of outcomes plus a flag instead of an invented sequence.
    """
    half = tick / 2
    stop_hit = low <= stop_trigger + EPS
    stop_gap = low < stop_limit - half          # the path went below the limit: a skip is possible
    tgt_through = high >= target + tick - half
    tgt_touch = abs(high - target) < half
    if stop_hit and (tgt_through or tgt_touch):
        return {"pessimistic": "STOP_SKIP_POSSIBLE" if stop_gap else "STOP",
                "optimistic": "TARGET" if tgt_through else "STOP", "flag": "AMBIGUOUS_ORDER"}
    if stop_hit:
        return {"pessimistic": "STOP_SKIP_POSSIBLE" if stop_gap else "STOP", "optimistic": "STOP",
                "flag": "STOP_GAP" if stop_gap else "NONE"}
    if tgt_through:
        return {"pessimistic": "TARGET", "optimistic": "TARGET", "flag": "NONE"}
    if tgt_touch:
        return {"pessimistic": "NO_FILL", "optimistic": "TARGET", "flag": "TOUCH_ONLY"}
    return {"pessimistic": "NONE", "optimistic": "NONE", "flag": "NONE"}


def atp_interval_vwap_error_bound(v_prev: int, v_now: int, atp_quantum: float = 0.01) -> float:
    """Worst-case error of p_bar = (ATP_t V_t - ATP_{t-1} V_{t-1}) / (V_t - V_{t-1}).

    ATP is disseminated in paise, so each ATP carries up to one quantum of error.
    The error is amplified by V/dV: after the first minutes of a session it spans
    many ticks, which is why this module does not use the "exact ATP decomposition".
    """
    dv = v_now - v_prev
    if dv <= 0:
        return math.inf
    return atp_quantum * (v_now + v_prev) / dv


# ------------------------------------------------------ planning forecast
def fill_probability(ahead: float, qty: int, horizon_s: float, *, trade_rate_per_s: float,
                     mean_trade_size: float, second_moment_size: float, cancel_rate_per_s: float = 0.0) -> float:
    """P(a resting order of `qty` behind `ahead` is fully filled within `horizon_s`).

    Same-side prints at our price arrive as a compound Poisson process (rate lambda, sizes
    with mean m and second moment m2); orders ahead cancel at rate kappa each, so the
    queue ahead decays to ahead * exp(-kappa H). Full fill needs V_H >= ahead e^{-kappa H} + qty:
        P ~ 1 - Phi((ahead e^{-kappa H} + qty - lambda m H) / sqrt(lambda m2 H))
    A normal approximation for ranking and planning. It is never fill evidence: only the
    snapshot envelopes above can book a fill.
    """
    if horizon_s <= 0 or trade_rate_per_s <= 0:
        return 0.0
    need = ahead * math.exp(-cancel_rate_per_s * horizon_s) + qty
    mu = trade_rate_per_s * mean_trade_size * horizon_s
    sd = math.sqrt(trade_rate_per_s * second_moment_size * horizon_s)
    return 0.5 * math.erfc((need - mu) / (sd * math.sqrt(2.0)))
