"""Market-data contract for Track 2 execution realism.

Fixes the preconditions every fill model depends on:
  R01  a missing or invalid quote is an error, never replaced by an intended price
  R07  one typed snapshot contract, filled by a per-broker adapter
  R08  cumulative day volume becomes per-interval deltas with explicit gap/reset flags
  R09  freshness is per symbol and is updated only after a packet validates

Retail feeds (Kite "full", Dhan "Full") deliver conflated MBP snapshots: best 5
price levels (price, quantity, order count), LTP, LTQ, LTT, ATP, cumulative
volume. Neither exposes individual trades, aggressor side or order IDs.
Everything downstream is built on that fact.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, FrozenSet, Iterable, Optional, Tuple

SYMBOL_RE = re.compile(r"^[A-Z0-9&\-]{1,20}$")
EPS = 1e-9


class FeedInvalid(Exception):
    """Market data cannot be used. Callers freeze state; they never substitute."""


class Session(str, Enum):
    PRE_OPEN = "PRE_OPEN"
    CONTINUOUS = "CONTINUOUS"
    CLOSING_AUCTION = "CLOSING_AUCTION"   # F&O stocks since 3-Aug-2026: no SL orders
    HALTED = "HALTED"                     # market-wide circuit breaker: unmatched orders purged
    CLOSED = "CLOSED"


# --------------------------------------------------------------------- ticks
def price_to_ticks(price: float, tick: float) -> int:
    if not (isinstance(price, (int, float)) and math.isfinite(price)):
        raise ValueError("price is not finite")
    if not (isinstance(tick, (int, float)) and math.isfinite(tick) and tick > 0):
        raise ValueError("tick must be finite and positive")
    n = round(price / tick)
    if abs(n * tick - price) > tick * 1e-6:
        raise ValueError(f"price {price} is off the {tick} tick grid")
    return int(n)


def on_grid(price: float, tick: float) -> bool:
    try:
        price_to_ticks(price, tick)
        return True
    except ValueError:
        return False


def ticks_to_price(n: int, tick: float) -> float:
    return round(n * tick, 10)


def floor_to_tick(price: float, tick: float) -> float:
    return ticks_to_price(math.floor(price / tick + 1e-9), tick)


def ceil_to_tick(price: float, tick: float) -> float:
    return ticks_to_price(math.ceil(price / tick - 1e-9), tick)


def schedule_tick(reference_close: float) -> float:
    """NSE CM price-based tick schedule, for CROSS-CHECKING the instrument master only.

    <250: 0.01 (NSE/CMTR/62174, eff. 10-Jun-2024); 250-1,000: 0.05; >1,000-5,000: 0.10;
    >5,000-10,000: 0.50; >10,000-20,000: 1.00; >20,000: 5.00 (NSE/CMTR/67133, eff.
    15-Apr-2025). The tick is fixed monthly from the previous month's last close, so
    today's price does not determine it. The broker instrument master is authoritative.
    """
    p = float(reference_close)
    if not math.isfinite(p) or p <= 0:
        raise ValueError("reference close must be finite and positive")
    if p < 250:
        return 0.01
    if p <= 1000:
        return 0.05
    if p <= 5000:
        return 0.10
    if p <= 10000:
        return 0.50
    if p <= 20000:
        return 1.00
    return 5.00


# ----------------------------------------------------------------- snapshot
@dataclass(frozen=True)
class Level:
    price: float
    qty: int
    orders: int


@dataclass(frozen=True)
class Snapshot:
    """One conflated market-by-price packet for one symbol."""
    symbol: str
    source: str                 # "KITE_FULL" | "DHAN_FULL" | "DHAN_DEPTH20" | "REPLAY"
    seq: int                    # adapter-assigned, strictly increasing per symbol
    recv_ts: float              # local receipt time, epoch seconds (NTP-disciplined clock)
    session: Session
    ltp: float
    cum_volume: int             # exchange cumulative day volume (includes pre-open trade)
    day_high: float
    day_low: float
    bids: Tuple[Level, ...]     # best first (descending price)
    asks: Tuple[Level, ...]     # best first (ascending price)
    depth_levels: int = 5       # levels this feed can display (5, or 20 for Dhan depth-20)
    ltt: Optional[float] = None         # last trade time (exchange clock)
    ltq: Optional[int] = None           # last trade quantity
    atp: Optional[float] = None         # session VWAP, quantised to the paisa by the exchange
    exch_ts: Optional[float] = None     # exchange packet timestamp (Kite provides; Dhan does not)
    total_buy_qty: Optional[int] = None
    total_sell_qty: Optional[int] = None

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0].price if self.asks else None

    def side_levels(self, side: str) -> Tuple[Level, ...]:
        if side == "BID":
            return self.bids
        if side == "ASK":
            return self.asks
        raise ValueError("side must be BID or ASK")

    def qty_at(self, side: str, price: float, tick: float) -> Optional[int]:
        """Displayed quantity at `price`.

        0    -> observably empty (inside the displayed range, or better than the best)
        None -> beyond the displayed depth: NOT observable. Never read this as empty.
        """
        levels = self.side_levels(side)
        half = tick / 2.0
        for lv in levels:
            if abs(lv.price - price) < half:
                return lv.qty
        if not levels:
            return 0
        best, worst = levels[0].price, levels[-1].price
        if side == "BID":
            if price > best + half or price > worst - half:
                return 0
        else:
            if price < best - half or price < worst + half:
                return 0
        return 0 if len(levels) < self.depth_levels else None

    def displayed_qty(self, side: str) -> int:
        return sum(lv.qty for lv in self.side_levels(side))


def _finite_pos(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def _nonneg_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool) and x >= 0


def validate_snapshot(
    s: Snapshot,
    *,
    now: float,
    tick: float,
    max_age_s: float,
    universe: FrozenSet[str],
    prev: Optional[Snapshot] = None,
    max_exch_latency_s: float = 3.0,
    clock_skew_s: float = 1.0,
) -> None:
    """Raise FeedInvalid unless `s` is usable as a live quote for its own symbol."""

    def bad(msg: str) -> None:
        raise FeedInvalid(f"{getattr(s, 'symbol', '?')!r}: {msg}")

    if not isinstance(s.symbol, str) or not SYMBOL_RE.match(s.symbol):
        bad("invalid symbol")
    if s.symbol not in universe:
        bad("symbol not in the required universe")
    if not _nonneg_int(s.seq):
        bad("invalid seq")
    if not _finite_pos(s.recv_ts):
        bad("invalid recv_ts")
    age = now - s.recv_ts
    if age > max_age_s:
        bad(f"stale: age {age:.2f}s > {max_age_s}s")
    if age < -clock_skew_s:
        bad("recv_ts is in the future")
    if not isinstance(s.session, Session):
        bad("invalid session")
    if not _finite_pos(s.ltp):
        bad("LTP missing or non-positive")          # R01: never replaced by an intended price
    if not on_grid(s.ltp, tick):
        bad("LTP off the tick grid")
    if not (_finite_pos(s.day_high) and _finite_pos(s.day_low)) or s.day_low > s.day_high + EPS:
        bad("invalid day range")
    if not (s.day_low - tick / 2 <= s.ltp <= s.day_high + tick / 2):
        bad("LTP outside the day range")
    if not _nonneg_int(s.cum_volume):
        bad("invalid cumulative volume")
    if not (isinstance(s.depth_levels, int) and s.depth_levels >= 1):
        bad("invalid depth_levels")
    for side, levels, descending in (("bid", s.bids, True), ("ask", s.asks, False)):
        if len(levels) > s.depth_levels:
            bad(f"{side}: more levels than declared depth")
        last = None
        for lv in levels:
            if not _finite_pos(lv.price) or not on_grid(lv.price, tick):
                bad(f"{side}: invalid/off-grid price {lv.price}")
            if not _nonneg_int(lv.qty) or not _nonneg_int(lv.orders):
                bad(f"{side}: invalid qty/orders")
            if lv.qty > 0 and lv.orders < 1:
                bad(f"{side}: quantity without orders")
            if lv.qty == 0:
                bad(f"{side}: displayed level with zero quantity")
            if last is not None and ((descending and lv.price >= last - EPS) or (not descending and lv.price <= last + EPS)):
                bad(f"{side}: levels not strictly ordered")
            last = lv.price
    if s.session is Session.CONTINUOUS and s.bids and s.asks and s.bids[0].price >= s.asks[0].price - EPS:
        bad("crossed or locked book in the continuous session")
    if s.exch_ts is not None:
        if not _finite_pos(s.exch_ts) or s.exch_ts > s.recv_ts + clock_skew_s:
            bad("exchange timestamp invalid or ahead of receipt")
        if s.recv_ts - s.exch_ts > max_exch_latency_s:
            bad("exchange-to-receipt latency too high")
    if s.ltt is not None and (not _finite_pos(s.ltt) or s.ltt > s.recv_ts + clock_skew_s):
        bad("last trade time invalid")
    if s.ltq is not None and not _nonneg_int(s.ltq):
        bad("invalid LTQ")
    if s.atp is not None:
        if not _finite_pos(s.atp) or not (s.day_low - tick <= s.atp <= s.day_high + tick):
            bad("ATP invalid or outside the day range")
    for name in ("total_buy_qty", "total_sell_qty"):
        v = getattr(s, name)
        if v is not None and not _nonneg_int(v):
            bad(f"invalid {name}")
    if prev is not None:
        if prev.symbol != s.symbol:
            bad("previous snapshot belongs to another symbol")
        if s.seq <= prev.seq:
            bad("non-increasing seq")
        if s.recv_ts < prev.recv_ts:
            bad("recv_ts went backwards")
        if s.day_high < prev.day_high - EPS or s.day_low > prev.day_low + EPS:
            bad("day range shrank: inconsistent session statistics")
        if s.ltt is not None and prev.ltt is not None and s.ltt < prev.ltt - EPS:
            bad("last trade time went backwards")


class FreshnessRegistry:
    """Per-symbol last valid snapshot (R09).

    A packet can refresh only its own symbol, and only after it validates. An
    unknown or malformed packet changes nothing, so it cannot certify stale data.
    """

    def __init__(self, universe: Iterable[str], tick_of: Dict[str, float], max_age_s: float = 2.0):
        self.universe = frozenset(universe)
        if not self.universe:
            raise ValueError("empty universe")
        self.tick_of = dict(tick_of)
        missing = sorted(self.universe - set(self.tick_of))
        if missing:
            raise ValueError(f"no tick size for {missing} (load the instrument master first)")
        self.max_age_s = float(max_age_s)
        self._last: Dict[str, Snapshot] = {}
        self.rejected = 0

    def start_session(self) -> None:
        self._last.clear()

    def accept(self, s: Snapshot, now: float) -> Snapshot:
        try:
            tick = self.tick_of.get(getattr(s, "symbol", None))
            if tick is None:
                raise FeedInvalid(f"{getattr(s, 'symbol', '?')!r}: symbol not in instrument master")
            validate_snapshot(s, now=now, tick=tick, max_age_s=self.max_age_s,
                              universe=self.universe, prev=self._last.get(s.symbol))
        except FeedInvalid:
            self.rejected += 1
            raise
        self._last[s.symbol] = s
        return s

    def latest(self, symbol: str, now: float) -> Snapshot:
        s = self._last.get(symbol)
        if s is None:
            raise FeedInvalid(f"{symbol!r}: no valid snapshot this session")
        if now - s.recv_ts > self.max_age_s:
            raise FeedInvalid(f"{symbol!r}: stale ({now - s.recv_ts:.2f}s)")
        return s

    def assert_complete(self, now: float) -> None:
        bad = [sym for sym in sorted(self.universe)
               if sym not in self._last or now - self._last[sym].recv_ts > self.max_age_s]
        if bad:
            raise FeedInvalid(f"universe incomplete or stale: {bad}")


# ------------------------------------------------------------------- volume
FIRST_OF_SESSION = "FIRST_OF_SESSION"
COUNTER_RESET = "COUNTER_RESET"
GAP = "GAP"


@dataclass(frozen=True)
class VolumeDelta:
    symbol: str
    t0: Optional[float]
    t1: float
    delta: Optional[int]          # None = unknowable for this interval
    flags: FrozenSet[str]


class VolumeDeltaTracker:
    """Cumulative day volume -> per-interval deltas (R08).

    Only the increment is new volume. The first packet of a session has no
    baseline; a decreasing counter (reconnect, feed switch) makes the interval
    unknowable rather than negative or doubled.
    """

    def __init__(self, gap_s: float = 5.0):
        self.gap_s = float(gap_s)
        self._last: Dict[Tuple[str, str], Tuple[float, int]] = {}

    def update(self, s: Snapshot, session_date: str) -> VolumeDelta:
        key = (s.symbol, session_date)
        prev = self._last.get(key)
        self._last[key] = (s.recv_ts, s.cum_volume)
        if prev is None:
            return VolumeDelta(s.symbol, None, s.recv_ts, None, frozenset({FIRST_OF_SESSION}))
        t0, v0 = prev
        if s.cum_volume < v0:
            return VolumeDelta(s.symbol, t0, s.recv_ts, None, frozenset({COUNTER_RESET}))
        flags = frozenset({GAP}) if s.recv_ts - t0 > self.gap_s else frozenset()
        return VolumeDelta(s.symbol, t0, s.recv_ts, s.cum_volume - v0, flags)


@dataclass(frozen=True)
class BucketVolume:
    lo: int      # volume certainly in the bucket
    hi: int      # volume possibly in the bucket


def split_across_boundary(delta: int, t0: float, t1: float, boundary: float,
                          ltt_curr: Optional[float], ltq_curr: Optional[int]) -> Tuple[BucketVolume, BucketVolume]:
    """Split an interval's volume between the bucket ending at `boundary` and the next.

    All times on the exchange clock. If the latest trade in (t0, t1] happened before
    the boundary, every trade did. Otherwise at least the last trade's quantity is in
    the later bucket and the rest is ambiguous. Volume confirmation uses `lo`.
    """
    if delta < 0:
        raise ValueError("negative delta")
    if t1 <= boundary:
        return BucketVolume(delta, delta), BucketVolume(0, 0)
    if t0 >= boundary:
        return BucketVolume(0, 0), BucketVolume(delta, delta)
    if ltt_curr is not None and ltt_curr < boundary:
        return BucketVolume(delta, delta), BucketVolume(0, 0)
    later_certain = min(delta, ltq_curr) if (ltt_curr is not None and ltq_curr) else 0
    return BucketVolume(0, delta - later_certain), BucketVolume(later_certain, delta)
