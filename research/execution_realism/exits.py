"""Pillar 3: stops, emergency exits and the dynamic price band (R05).

An exit instruction is not an exit. A position is flat only when exit fills
covering its full quantity have evidence. Everything else is a live state:
queued at the band, purged by a market-wide halt, rejected by the broker,
waiting for the closing auction, or failed and escalated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

from .fills import (DEFAULT_HAIRCUTS, PESS, Envelope, Evidence, FillEvent, PassiveOrderSim, QueueParams,
                    SelfConsumption, Side, taker_fill)
from .marketdata import EPS, Session, Snapshot, VolumeDelta, ceil_to_tick, floor_to_tick

# ------------------------------------------------------------ dynamic band
# NSE/FAOP/63405 (14-Aug-2024, eff. 19-Aug-2024): flex steps are % of yesterday's
# close: +5, +5 (cooling 15 min, or 5 min if met in the last half hour), +3, +3
# (30 min), then +2 each (60 min). Trigger: last trade within 0.10% of base of the
# band edge (9.90%, 14.90%, ...). Criteria: >=50 trades, 10 UCCs, 3 TMs per side
# (NSE/FAOP/62241). Since NSE/FAOP/64995 (eff. 18-Nov-2024) the whole band slides:
# base 100 flexed up gives 95-115, and resting limit orders outside the new band are
# cancelled; untriggered SL orders are kept but accepted on trigger only if their
# limit is inside the prevailing band. Trading continues inside the old band during
# cooling-off: there is no "cooling freeze".
FLEX_STEPS_PCT = (5.0, 5.0, 3.0, 3.0)
LATER_STEP_PCT = 2.0
COOLING_MIN = (15, 15, 30, 30)
LATER_COOLING_MIN = 60
LAST_HALF_HOUR_COOLING_MIN = 5
TRIGGER_GAP_PCT = 0.10


@dataclass
class DynamicBand:
    prev_close: float
    tick: float
    continuous_end_ts: float            # 15:15 IST for CAS (F&O) stocks since 3-Aug-2026
    lo_pct: float = -10.0
    hi_pct: float = 10.0
    flexes: int = 0                     # counted across both directions (circular is silent)
    pending_dir: Optional[str] = None
    pending_at: Optional[float] = None
    history: List[Tuple[float, str, bool]] = field(default_factory=list)

    @property
    def lower(self) -> float:
        return ceil_to_tick(self.prev_close * (1 + self.lo_pct / 100.0), self.tick)

    @property
    def upper(self) -> float:
        return floor_to_tick(self.prev_close * (1 + self.hi_pct / 100.0), self.tick)

    def contains(self, price: float) -> bool:
        return self.lower - EPS <= price <= self.upper + EPS

    @staticmethod
    def step_pct(k: int) -> float:
        return FLEX_STEPS_PCT[k] if k < len(FLEX_STEPS_PCT) else LATER_STEP_PCT

    def cooling_s(self, k: int, t: float) -> float:
        if k < 2:
            last_half_hour = t >= self.continuous_end_ts - 1800
            return 60.0 * (LAST_HALF_HOUR_COOLING_MIN if last_half_hour else COOLING_MIN[k])
        return 60.0 * (COOLING_MIN[k] if k < len(COOLING_MIN) else LATER_COOLING_MIN)

    def up_trigger(self) -> float:
        return self.prev_close * (1 + (self.hi_pct - TRIGGER_GAP_PCT) / 100.0)

    def down_trigger(self) -> float:
        return self.prev_close * (1 + (self.lo_pct + TRIGGER_GAP_PCT) / 100.0)

    def on_trade(self, price: float, t: float) -> Optional[str]:
        """Register a pending flex when a print reaches the trigger. Returns its direction."""
        if self.pending_dir is not None:
            return None
        if price >= self.up_trigger() - EPS:
            d = "UP"
        elif price <= self.down_trigger() + EPS:
            d = "DOWN"
        else:
            return None
        self.pending_dir, self.pending_at = d, t + self.cooling_s(self.flexes, t)
        return d

    def resolve_pending(self, t: float, criteria_met: bool) -> Optional[Tuple[float, float]]:
        """At the end of cooling-off, slide the band if the trade/UCC/TM criteria held.
        Retail feeds cannot observe UCC/TM counts: callers pass the branch they are
        stress-testing (the adverse one for risk)."""
        if self.pending_dir is None or t < self.pending_at:
            return None
        if criteria_met:
            step = self.step_pct(self.flexes) * (1 if self.pending_dir == "UP" else -1)
            self.lo_pct += step
            self.hi_pct += step
            self.flexes += 1
        self.history.append((t, self.pending_dir, criteria_met))
        self.pending_dir = self.pending_at = None
        return self.lower, self.upper

    def adverse_mark_long(self) -> float:
        """Mark for a long pinned at the lower band: the lower band after one more down-flex."""
        return ceil_to_tick(self.prev_close * (1 + (self.lo_pct - self.step_pct(self.flexes)) / 100.0), self.tick)


# ----------------------------------------------------------- rate limiting
class TokenBucket:
    """Order-rate limiter shared by every order the desk sends (10 orders/second is
    the retail-algo threshold; brokers answer excess with HTTP 429)."""

    def __init__(self, rate_per_s: float = 10.0, burst: int = 10):
        self.rate, self.burst, self.tokens, self.t = rate_per_s, burst, float(burst), None

    def take(self, now: float) -> bool:
        if self.t is not None:
            self.tokens = min(self.burst, self.tokens + (now - self.t) * self.rate)
        self.t = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


def _trade_in_interval(p: Snapshot, s: Snapshot, vd: VolumeDelta) -> bool:
    return (vd.delta is not None and vd.delta > 0) or (s.ltt is not None and p.ltt is not None and s.ltt > p.ltt + EPS)


# ------------------------------------------------------------ stop-limit
class StopState(str, Enum):
    ARMED = "ARMED"
    WORKING = "WORKING"
    RESTING = "RESTING"
    FILLED = "FILLED"
    SKIPPED = "SKIPPED"
    REJECTED_AT_TRIGGER = "REJECTED_AT_TRIGGER"
    PURGED = "PURGED"
    EXPIRED_AT_CONTINUOUS_CLOSE = "EXPIRED_AT_CONTINUOUS_CLOSE"


STOP_TERMINAL = {StopState.FILLED, StopState.SKIPPED, StopState.REJECTED_AT_TRIGGER, StopState.PURGED,
                 StopState.EXPIRED_AT_CONTINUOUS_CLOSE}


class StopLimitSell:
    """Exchange-held SL-limit sell protecting a long. Triggers on LTP (NSE/CMTR/54851
    wording); after trigger it is an ordinary limit at `limit`. If the market is already
    below the limit it rests above the market and does not protect you: SKIPPED."""

    def __init__(self, symbol: str, qty: int, trigger: float, limit: float, tick: float, *,
                 envelope: Envelope = PESS, haircuts: Sequence[float] = DEFAULT_HAIRCUTS,
                 params: QueueParams = QueueParams(), skip_confirm_s: float = 2.0):
        if not (0 < limit <= trigger):
            raise ValueError("sell stop needs 0 < limit <= trigger")
        self.symbol, self.qty, self.trigger, self.limit, self.tick = symbol, qty, trigger, limit, tick
        self.envelope, self.haircuts, self.params, self.skip_confirm_s = envelope, tuple(haircuts), params, skip_confirm_s
        self.state = StopState.ARMED
        self.filled = 0
        self.fills: List[FillEvent] = []
        self.passive: Optional[PassiveOrderSim] = None
        self.memory = {e: SelfConsumption() for e in Envelope}
        self.triggered_at: Optional[float] = None
        self._below_since: Optional[float] = None
        self._passive_seen = 0

    @property
    def remaining(self) -> int:
        return self.qty - self.filled

    def _take(self, p: Snapshot, s: Snapshot) -> None:
        res = taker_fill(Side.SELL, self.remaining, self.limit, self.tick, p, s, s.recv_ts,
                         haircuts=self.haircuts, memory=self.memory)
        for ev in res.events:
            if ev.envelope is self.envelope:
                self.fills.append(ev)
        self.filled += res.filled[self.envelope]

    def on_snapshot(self, p: Snapshot, s: Snapshot, vd: VolumeDelta, band: DynamicBand) -> StopState:
        if self.state in STOP_TERMINAL:
            return self.state
        if s.session is Session.HALTED:
            self.state = StopState.PURGED
            return self.state
        if s.session is not Session.CONTINUOUS:
            if s.session in (Session.CLOSING_AUCTION, Session.CLOSED):
                self.state = StopState.EXPIRED_AT_CONTINUOUS_CLOSE     # no SL orders in the closing auction
            return self.state
        if self.state is StopState.ARMED:
            hit = (_trade_in_interval(p, s, vd) and s.ltp <= self.trigger + EPS) or \
                  (s.day_low < p.day_low - EPS and s.day_low <= self.trigger + EPS)
            if not hit:
                return self.state
            self.triggered_at = s.recv_ts
            if not band.contains(self.limit):
                self.state = StopState.REJECTED_AT_TRIGGER
                return self.state
            self.state = StopState.WORKING
        if self.state is StopState.WORKING:
            if s.best_bid is not None and s.best_bid >= self.limit - EPS:
                self._take(p, s)
                if self.remaining == 0:
                    self.state = StopState.FILLED
                return self.state
            self.passive = PassiveOrderSim(f"{self.symbol}-SL", Side.SELL, self.limit, self.remaining,
                                           self.tick, self.params)
            self.passive.join(s)
            self.state = StopState.RESTING
        elif self.state is StopState.RESTING:
            self.passive.on_snapshot(s, vd)
            new = [ev for ev in self.passive.events[self._passive_seen:] if ev.envelope is self.envelope]
            self._passive_seen = len(self.passive.events)
            for ev in new:
                self.fills.append(ev)
                self.filled += ev.qty
            if self.remaining == 0:
                self.state = StopState.FILLED
                return self.state
        if s.ltp < self.limit - self.tick + EPS:
            self._below_since = self._below_since if self._below_since is not None else s.recv_ts
            if s.recv_ts - self._below_since >= self.skip_confirm_s:
                self.state = StopState.SKIPPED
        else:
            self._below_since = None
        return self.state


# ------------------------------------------------------------ emergency exit
class ExitState(str, Enum):
    CANCELLING_PROTECTIVE = "CANCELLING_PROTECTIVE"
    WORKING = "WORKING"
    PARTIAL = "PARTIAL"
    QUEUED_AT_BAND = "QUEUED_AT_BAND"
    PURGED = "PURGED"
    CAS_PENDING = "CAS_PENDING"
    REJECTED_RETRYING = "REJECTED_RETRYING"
    FAILED_ESCALATE = "FAILED_ESCALATE"
    FILLED = "FILLED"


@dataclass(frozen=True)
class ExitParams:
    collar_bps: float = 50.0
    collar_growth: float = 1.5
    max_collar_bps: float = 300.0
    retry_interval_s: float = 1.0
    order_latency_s: float = 0.25
    cancel_ack_latency_s: float = 0.5
    max_rejects: int = 3
    envelope: Envelope = PESS
    haircuts: Tuple[float, ...] = DEFAULT_HAIRCUTS


class EmergencyExitSim:
    """EMERGENCY_TRIGGERED -> attempts, never a completed transaction by fiat.

    1. Cancel protective orders (stop, target) first. Until the cancel is acknowledged
       no exit quantity is sent, so the desk can never sell more than it holds.
    2. Marketable-limit IOC sells at max(lower band, bid x (1 - collar)), widening the
       collar each retry, rate-limited, with self-consumption memory across attempts.
    3. No bids, or bids at the lower band: rest a DAY sell at the band (queue model).
    4. Market-wide halt: every order is purged; re-arm when trading resumes.
    5. After the continuous session: only the closing auction remains.
    FILLED only when evidenced exit fills cover the position.
    """

    def __init__(self, symbol: str, position_qty: int, tick: float, band: DynamicBand, t_trigger: float, *,
                 protective_open_qty: int = 0, params: ExitParams = ExitParams(),
                 limiter: Optional[TokenBucket] = None, queue_params: QueueParams = QueueParams()):
        if position_qty <= 0:
            raise ValueError("nothing to exit")
        self.symbol, self.tick, self.band, self.params = symbol, tick, band, params
        self.position = position_qty
        self.sold = 0
        self.fills: List[FillEvent] = []
        self.limiter = limiter or TokenBucket()
        self.queue_params = queue_params
        self.memory = {e: SelfConsumption() for e in Envelope}
        self.collar_bps = params.collar_bps
        self.rejects = 0
        self.attempts = 0
        self._pending: Optional[Tuple[float, int, float, Snapshot]] = None
        self._last_attempt = -math.inf
        self.passive: Optional[PassiveOrderSim] = None
        self._passive_seen = 0
        self.protective_unconfirmed = protective_open_qty
        self.cancel_effective_at = t_trigger + (params.cancel_ack_latency_s if protective_open_qty else 0.0)
        self.state = ExitState.CANCELLING_PROTECTIVE if protective_open_qty else ExitState.WORKING
        self.log: List[Tuple[float, str]] = []

    @property
    def remaining(self) -> int:
        return self.position - self.sold

    def protective_fill_during_race(self, qty: int) -> None:
        """A stop or target filled before its cancel landed: the position shrank."""
        qty = min(qty, self.remaining)
        self.position -= qty
        self.protective_unconfirmed = max(0, self.protective_unconfirmed - qty)
        if self.remaining == 0:
            self.state = ExitState.FILLED

    def _record(self, events: List[FillEvent]) -> None:
        for ev in events:
            if ev.envelope is self.params.envelope and ev.qty > 0:
                take = min(ev.qty, self.remaining)
                self.fills.append(FillEvent(ev.envelope, take, ev.price, ev.evidence, ev.seq, ev.ts))
                self.sold += take

    def on_snapshot(self, p: Snapshot, s: Snapshot, vd: VolumeDelta, *, now: float,
                    broker_reject: Optional[str] = None, cas_price: Optional[float] = None) -> ExitState:
        if self.state in (ExitState.FILLED, ExitState.FAILED_ESCALATE):
            return self.state
        if s.session is Session.HALTED:
            self._pending, self.passive = None, None
            self.state = ExitState.PURGED
            self.log.append((now, "PURGED_BY_HALT"))
            return self.state
        if s.session in (Session.CLOSING_AUCTION, Session.CLOSED):
            self._pending, self.passive = None, None
            self.state = ExitState.CAS_PENDING
            if cas_price is not None and self.band.lower <= cas_price + EPS and self.remaining > 0:
                self.fills.append(FillEvent(self.params.envelope, self.remaining, cas_price,
                                            Evidence.AUCTION, s.seq, now))
                self.sold = self.position
                self.state = ExitState.FILLED
            return self.state
        if s.session is not Session.CONTINUOUS:
            return self.state
        if self.state is ExitState.CANCELLING_PROTECTIVE:
            if now < self.cancel_effective_at:
                return self.state
            self.protective_unconfirmed = 0
            self.state = ExitState.WORKING
        if self.state is ExitState.PURGED:
            self.state = ExitState.WORKING
            self.log.append((now, "REARM_AFTER_HALT"))
        if self._pending is not None:
            limit, qty, arrival, before = self._pending
            if s.recv_ts < arrival:
                return self.state
            self._pending = None
            if broker_reject:
                self.rejects += 1
                self.log.append((now, f"REJECT:{broker_reject}"))
                self.state = (ExitState.FAILED_ESCALATE if self.rejects > self.params.max_rejects
                              else ExitState.REJECTED_RETRYING)
                return self.state
            res = taker_fill(Side.SELL, min(qty, self.remaining), limit, self.tick, before, s, arrival,
                             haircuts=self.params.haircuts, memory=self.memory)
            self._record(res.events)
            if self.remaining == 0:
                self.state = ExitState.FILLED
                return self.state
            self.state = ExitState.PARTIAL if self.sold else ExitState.WORKING
            self.collar_bps = min(self.params.max_collar_bps, self.collar_bps * self.params.collar_growth)
        if self.passive is not None:
            self.passive.on_snapshot(s, vd)
            new = self.passive.events[self._passive_seen:]
            self._passive_seen = len(self.passive.events)
            self._record(new)
            if self.remaining == 0:
                self.state = ExitState.FILLED
            return self.state
        if now - self._last_attempt < self.params.retry_interval_s or not self.limiter.take(now):
            return self.state
        bb = s.best_bid
        if bb is None or bb < self.band.lower + EPS:
            at_band = s.ltp <= self.band.lower + self.tick + EPS
            if bb is not None and bb >= self.band.lower - EPS:
                pass                                   # bid sits exactly at the band: marketable at band
            elif at_band:
                self.passive = PassiveOrderSim(f"{self.symbol}-EXIT", Side.SELL, self.band.lower,
                                               self.remaining, self.tick, self.queue_params)
                self.passive.join(s)
                self._last_attempt = now
                self.attempts += 1
                self.state = ExitState.QUEUED_AT_BAND
                self.log.append((now, "QUEUED_AT_LOWER_BAND"))
                return self.state
            else:
                return self.state                      # no bids, not at band: wait for a quote
        raw = (bb if bb is not None else self.band.lower) * (1 - self.collar_bps / 1e4)
        limit = max(self.band.lower, floor_to_tick(raw, self.tick))
        self._pending = (limit, self.remaining, s.recv_ts + self.params.order_latency_s, s)
        self._last_attempt = now
        self.attempts += 1
        self.log.append((now, f"IOC_SELL {self.remaining}@{limit}"))
        return self.state

    def mark_to_market(self, s: Snapshot) -> Optional[float]:
        """Conservative average mark for the unsold quantity: haircut bid walk, then the
        adverse band mark for whatever the displayed bids cannot absorb. Never entry price."""
        rem = self.remaining
        if rem == 0:
            return None
        value, left = 0.0, rem
        for k, lv in enumerate(s.bids):
            take = min(left, int(math.floor(self.params.haircuts[min(k, len(self.params.haircuts) - 1)] * lv.qty)))
            value += take * lv.price
            left -= take
            if left == 0:
                break
        value += left * self.band.adverse_mark_long()
        return value / rem


# --------------------------------------------------------------- costs
def exit_cost_estimate(qty: int, s: Snapshot, *, sigma_daily: float, adv_shares: float, sigma_1s: float,
                       latency_s: float, Y: float = 1.0, z: float = 1.645,
                       haircuts: Sequence[float] = DEFAULT_HAIRCUTS) -> Dict[str, float]:
    """Pre-trade cost of selling `qty` now, in bps of mid.

    book_bps     haircut walk of the displayed bids
    residual     quantity beyond the haircut depth, priced by the square-root law
                 Y * sigma_daily * sqrt(Q/ADV) below the last visible bid (Y ~ O(1),
                 international estimates; no Indian calibration exists)
    latency_bps  z * sigma_1s * sqrt(latency): the move you can suffer before arrival
    """
    if not (s.bids and s.asks):
        raise ValueError("two-sided book required for a cost estimate")
    mid = (s.bids[0].price + s.asks[0].price) / 2
    left, value, last_px = qty, 0.0, s.bids[0].price
    for k, lv in enumerate(s.bids):
        take = min(left, int(math.floor(haircuts[min(k, len(haircuts) - 1)] * lv.qty)))
        value += take * lv.price
        left -= take
        last_px = lv.price
        if left == 0:
            break
    residual_frac = Y * sigma_daily * math.sqrt(left / adv_shares) if left > 0 else 0.0
    value += left * last_px * (1 - residual_frac)
    vwap = value / qty
    return {
        "mid": mid,
        "vwap": vwap,
        "book_bps": (mid - vwap) / mid * 1e4,
        "residual_qty": float(left),
        "sqrt_law_whole_order_bps": Y * sigma_daily * math.sqrt(qty / adv_shares) * 1e4,
        "latency_bps": z * sigma_1s * math.sqrt(latency_s) * 1e4,
    }


# ------------------------------------------------------- skip probability
SIEGMUND_BETA = 0.5825971579390106   # -zeta(1/2) / sqrt(2*pi)


def expected_discrete_overshoot(sigma_per_sqrt_s: float, dt_s: float) -> float:
    """Mean overshoot of a diffusion first observed past a level on a dt grid
    (Siegmund's corrected-diffusion constant; Broadie-Glasserman-Kou 1997)."""
    return SIEGMUND_BETA * sigma_per_sqrt_s * math.sqrt(dt_s)


def wilson_interval(k: int, n: int, z: float = 1.96) -> Tuple[float, float]:
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0.0, c - h), min(1.0, c + h)


def skip_events(path: Sequence[Snapshot], trigger: float, theta: float) -> List[Dict[str, float]]:
    """Every first down-crossing of `trigger` along a snapshot path. `skipped` when the
    first snapshot at/after the crossing has no bid at or above trigger x (1 - theta)."""
    out, above = [], None
    limit = trigger * (1 - theta)
    for i in range(1, len(path)):
        p, s = path[i - 1], path[i]
        if above is None:
            above = p.ltp > trigger
        crossed = above and (s.ltp <= trigger or (s.day_low < p.day_low and s.day_low <= trigger))
        if crossed:
            bb = s.best_bid
            out.append({"t": s.recv_ts, "best_bid": bb if bb is not None else float("nan"),
                        "skipped": float(bb is None or bb < limit - EPS)})
        above = s.ltp > trigger
    return out


def skip_probability(events: Sequence[Dict[str, float]]) -> Dict[str, float]:
    n = len(events)
    k = int(sum(e["skipped"] for e in events))
    lo, hi = wilson_interval(k, n)
    return {"n": n, "k": k, "p": (k / n) if n else float("nan"), "lo95": lo, "hi95": hi}


def overnight_skip_probability(gap_returns: Sequence[float], stop_distance: float, theta: float) -> Dict[str, float]:
    """Runner held overnight with its stop `stop_distance` below the close: the stop-limit
    (or GTT) is skipped when the open prints below stop x (1 - theta)."""
    thr = (1 - stop_distance) * (1 - theta) - 1
    n = len(gap_returns)
    k = sum(1 for r in gap_returns if r < thr)
    lo, hi = wilson_interval(k, n)
    return {"n": n, "k": k, "p": (k / n) if n else float("nan"), "lo95": lo, "hi95": hi, "gap_threshold": thr}
