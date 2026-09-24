"""Review-only reference components. No broker access, qualification or live orders.

Analytical floats are deliberate here; production orders require integer ticks/paise.
Caller must supply authenticated, fresh, point-in-time inputs. No synthetic E3.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from datetime import datetime
import json
import math
from typing import Any, Callable, Mapping


def number(value: float, minimum: float = -math.inf) -> None:
    if type(value) not in (int, float) or not math.isfinite(value) or value < minimum:
        raise ValueError("invalid finite numeric input")


def integer(value: int, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValueError("invalid integer input")


@dataclass(frozen=True)
class Provenance:
    source: str
    observed_at: datetime
    received_at: datetime
    source_sha256: str
    schema_version: str = "research-1"

    def __post_init__(self) -> None:
        if not self.source or not self.schema_version:
            raise ValueError("source/version required")
        for stamp in (self.observed_at, self.received_at):
            if not isinstance(stamp, datetime) or stamp.utcoffset() is None:
                raise ValueError("aware timestamps required")
        if self.observed_at > self.received_at:
            raise ValueError("future source timestamp")
        if len(self.source_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.source_sha256):
            raise ValueError("SHA256 required")

    def require_fresh(self, now: datetime, max_age_seconds: float) -> None:
        number(max_age_seconds, 0)
        if now.utcoffset() is None or not 0 <= (now-self.observed_at).total_seconds() <= max_age_seconds:
            raise ValueError("stale/future evidence")


class Snapshot:
    def __post_init__(self) -> None:
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, float):
                number(value)
        if not isinstance(self.provenance, Provenance):
            raise ValueError("provenance required")

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=lambda v: v.isoformat(),
                          allow_nan=False, sort_keys=True)


@dataclass(frozen=True)
class DerivativesAlphaSnapshot(Snapshot):
    provenance: Provenance
    symbol: str
    expiry: str
    call_wall: float | None
    put_wall: float | None
    max_pain: float | None
    pcr_oi: float | None
    pcr_volume: float | None
    futures_basis: float | None
    futures_oi_change: float | None
    dealer_gex_rs_per_1pct: float | None
    rollover_velocity: float | None
    dealer_sign_observed: bool = False

    def __post_init__(self) -> None:
        super().__post_init__()
        if not self.symbol or not self.expiry:
            raise ValueError("symbol and expiry required")
        datetime.strptime(self.expiry, "%Y-%m-%d")
        for name in ("call_wall", "put_wall", "max_pain", "pcr_oi", "pcr_volume"):
            value = getattr(self, name)
            if value is not None:
                number(value, 0)
        for name in ("futures_basis", "futures_oi_change", "dealer_gex_rs_per_1pct", "rollover_velocity"):
            value = getattr(self, name)
            if value is not None:
                number(value)
        if type(self.dealer_sign_observed) is not bool:
            raise ValueError("boolean dealer provenance required")
        if self.dealer_gex_rs_per_1pct is not None and not self.dealer_sign_observed:
            raise ValueError("public OI cannot establish dealer GEX")


@dataclass(frozen=True)
class MultiLevelOFISnapshot(Snapshot):
    provenance: Provenance
    level_ofi: tuple[float, ...]
    normalized_ofi: float
    microprice: float
    spread_bps: float
    iceberg_detected: bool | None = None
    approximation: str = "AGGREGATE_DEPTH_SNAPSHOT"

    def __post_init__(self) -> None:
        super().__post_init__()
        if type(self.level_ofi) is not tuple or len(self.level_ofi) != 5:
            raise ValueError("five immutable levels required")
        for x in self.level_ofi:
            number(x)
        number(self.normalized_ofi, -1)
        number(self.microprice, .000001)
        number(self.spread_bps, 0)
        if self.normalized_ofi > 1 or self.iceberg_detected is not None and type(self.iceberg_detected) is not bool:
            raise ValueError("invalid OFI or iceberg status")


@dataclass(frozen=True)
class VIXRegimeClassification(Snapshot):
    provenance: Provenance
    vix: float
    tier: str
    strategy_multipliers: tuple[tuple[str, float], ...]
    calibration_status: str = "ASSUMED_NOT_VALIDATED"

    def __post_init__(self) -> None:
        super().__post_init__()
        number(self.vix, .000001)
        expected = "LOW" if self.vix < 11.5 else "NORMAL" if self.vix <= 16.5 else "ELEVATED" if self.vix <= 22 else "CRISIS"
        if self.tier != expected or type(self.strategy_multipliers) is not tuple:
            raise ValueError("invalid regime contract")
        names = set()
        for pair in self.strategy_multipliers:
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError("immutable multiplier pair required")
            name, value = pair
            number(value, 0)
            if not name or name in names or value > 1 or self.tier == "CRISIS" and value != 0:
                raise ValueError("invalid risk multiplier")
            names.add(name)


@dataclass(frozen=True)
class UnifiedEnsembleSignal(Snapshot):
    provenance: Provenance
    symbol: str
    strategy: str
    sector: str
    side: str
    conviction: float
    entry: float
    stop: float
    targets: tuple[tuple[int, float], ...]
    shares: int
    cost_allowance_rs: float
    slippage_allowance_rs: float
    derivatives_status: str = "NOT_USED"
    research_only: bool = True

    def __post_init__(self) -> None:
        super().__post_init__()
        integer(self.shares, 1)
        for x in (self.entry, self.stop):
            number(x, .000001)
        for x in (self.cost_allowance_rs, self.slippage_allowance_rs, self.conviction):
            number(x, 0)
        if not all((self.symbol, self.strategy, self.sector)) or self.side not in ("BUY", "SELL") or self.conviction > 1:
            raise ValueError("invalid signal identity or score")
        direction = 1 if self.side == "BUY" else -1
        if direction * (self.entry-self.stop) <= 0 or self.research_only is not True:
            raise ValueError("invalid stop or attempted live signal")
        if type(self.targets) is not tuple or not self.targets:
            raise ValueError("immutable targets required")
        total = 0
        for pair in self.targets:
            if type(pair) is not tuple or len(pair) != 2:
                raise ValueError("invalid target pair")
            qty, price = pair
            integer(qty, 1)
            number(price, .000001)
            total += qty
            if direction * (price-self.entry) <= 0:
                raise ValueError("target on wrong side")
        risk = self.shares*abs(self.entry-self.stop)+self.cost_allowance_rs+self.slippage_allowance_rs
        if total != self.shares or risk > 1500 or self.shares*self.entry > 58333.33:
            raise ValueError("risk/notional/quantity contract exceeded")


@dataclass(frozen=True)
class Depth:
    provenance: Provenance
    # Each row: bid, bid quantity, ask, ask quantity; best level first.
    levels: tuple[tuple[float, float, float, float], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.provenance, Provenance) or type(self.levels) is not tuple or len(self.levels) != 5:
            raise ValueError("five immutable depth levels required")
        for i, row in enumerate(self.levels):
            if type(row) is not tuple or len(row) != 4:
                raise ValueError("invalid depth row")
            b, qb, a, qa = row
            for x in row:
                number(x, .000001)
            if b >= a or i and (b >= self.levels[i-1][0] or a <= self.levels[i-1][2]):
                raise ValueError("crossed or unsorted depth")


class MultiLevelOFICalculator:
    weights = (1., .6, .35, .2, .1)

    @classmethod
    def calculate(cls, previous: Depth, current: Depth, now: datetime,
                  max_age_seconds: float = 2.) -> MultiLevelOFISnapshot:
        previous.provenance.require_fresh(now, max_age_seconds)
        current.provenance.require_fresh(now, max_age_seconds)
        if current.provenance.source != previous.provenance.source or current.provenance.observed_at <= previous.provenance.observed_at:
            raise ValueError("source mismatch or nonmonotonic snapshots")
        contributions, denominator = [], 0.
        for old, new, weight in zip(previous.levels, current.levels, cls.weights):
            b0, qb0, a0, qa0 = old
            b, qb, a, qa = new
            bid = qb if b > b0 else -qb0 if b < b0 else qb-qb0
            ask = -qa if a < a0 else qa0 if a > a0 else qa0-qa
            contributions.append(bid+ask)
            denominator += weight*(qb+qb0+qa+qa0)
        b, qb, a, qa = current.levels[0]
        return MultiLevelOFISnapshot(current.provenance, tuple(contributions),
            sum(w*e for w, e in zip(cls.weights, contributions))/denominator,
            (b*qa+a*qb)/(qb+qa), 10000*(a-b)/((a+b)/2))


class DerivativesOverlayEngine:
    @staticmethod
    def evaluate(snapshot: DerivativesAlphaSnapshot, entry: float, atr: float,
                 now: datetime, max_age_seconds: float = 60.) -> dict[str, Any]:
        snapshot.provenance.require_fresh(now, max_age_seconds)
        number(entry, .000001)
        number(atr, .000001)
        if datetime.strptime(snapshot.expiry, "%Y-%m-%d").date() < now.date():
            raise ValueError("expired derivative input")
        warnings = []
        if snapshot.call_wall is not None and 0 <= snapshot.call_wall-entry <= .35*atr:
            warnings.append("NEAR_CALL_OI_WALL_NOT_PROOF_OF_WRITING")
        if snapshot.futures_basis is not None and snapshot.futures_basis < 0:
            warnings.append("NEGATIVE_RAW_BASIS_CHECK_DIVIDENDS_AND_CARRY")
        if snapshot.pcr_oi is not None and (snapshot.pcr_oi < .65 or snapshot.pcr_oi > 1.4):
            warnings.append("PCR_EXTREME_ASSUMED_BOUNDARY")
        # Max pain and GEX are telemetry, not a validated approval model.
        return {"verdict": "RESEARCH_ONLY", "live_approved": False,
                "warnings": tuple(warnings), "max_pain": snapshot.max_pain,
                "dealer_gex": snapshot.dealer_gex_rs_per_1pct}


class CompassIntegrationBridge:
    @staticmethod
    def evaluate_portfolio_candidates(candidates: tuple[Mapping[str, Any], ...],
                                      sector_candle_matrix: Mapping[str, Any],
                                      market_candles_15m: Any,
                                      evaluator: Any,
                                      validate_batch: Callable[..., None]) -> tuple[Any, ...]:
        """Validation callback must reject stale/incomplete/nonaligned external data.

        Kept mandatory: silently constructing a partial schema here would recreate
        the production fallback defect. No allocation occurs in this adapter.
        """
        validate_batch(candidates, sector_candle_matrix, market_candles_15m)
        if not market_candles_15m or len(market_candles_15m) < 5:
            raise ValueError("missing market bars")
        results = []
        for candidate in candidates:
            symbol, sector = candidate["symbol"], candidate["sector"]
            peers = {s: bars for s, bars in sector_candle_matrix[sector].items() if s != symbol}
            if len(peers) < 3 or any(len(bars) < 5 for bars in peers.values()):
                raise ValueError("insufficient independent peers")
            number(candidate["stock_sector_beta"])
            number(candidate["bucket_median_vol"], .000001)
            number(candidate["current_ask"], .000001)
            results.append(evaluator.evaluate(symbol=symbol, sector=sector,
                candles_15m=candidate["candles_15m"], sector_constituents_candles=peers,
                market_candles_15m=market_candles_15m,
                bucket_median_vol=candidate["bucket_median_vol"],
                stock_sector_beta=candidate["stock_sector_beta"], current_ask=candidate["current_ask"]))
        return tuple(results)


@dataclass(frozen=True)
class QueueState:
    ahead: int
    quantity: int
    filled: int = 0
    seen_event_ids: tuple[str, ...] = ()
    state: str = "QUEUED"

    def __post_init__(self) -> None:
        integer(self.ahead)
        integer(self.quantity, 1)
        integer(self.filled)
        if self.filled > self.quantity or type(self.seen_event_ids) is not tuple:
            raise ValueError("invalid queue ledger")
        if self.state not in ("QUEUED", "PARTIAL", "FILLED", "LOCKED_NO_BID"):
            raise ValueError("invalid execution state")
        if len(set(self.seen_event_ids)) != len(self.seen_event_ids) or any(not isinstance(x, str) or not x for x in self.seen_event_ids):
            raise ValueError("invalid event identities")
        if (self.state == "FILLED") != (self.filled == self.quantity):
            raise ValueError("inconsistent terminal state")


class DiscreteExecutionSimulator:
    @staticmethod
    def step(old: QueueState, event_id: str, eligible_trade_qty: int = 0,
             known_cancel_ahead: int = 0, contra_available: bool = True) -> QueueState:
        """Eligible quantities must be incremental trades at this queue's price.

        Cancellation input is an explicit modeled event, not inferred from L2.
        Caller orders events and preserves returned state across replay/restart.
        """
        integer(eligible_trade_qty)
        integer(known_cancel_ahead)
        if not isinstance(event_id, str) or not event_id or event_id in old.seen_event_ids:
            raise ValueError("duplicate/invalid event ID")
        if type(contra_available) is not bool or known_cancel_ahead > old.ahead:
            raise ValueError("invalid contra or cancellation event")
        if old.state == "FILLED":
            return old
        if not contra_available and eligible_trade_qty:
            raise ValueError("contradictory no-contra/trade event")
        ahead = old.ahead-known_cancel_ahead
        added = min(old.quantity-old.filled, max(0, eligible_trade_qty-ahead))
        filled = old.filled+added
        ahead = max(0, ahead-eligible_trade_qty)
        state = "FILLED" if filled == old.quantity else "LOCKED_NO_BID" if not contra_available else "PARTIAL" if filled else "QUEUED"
        return QueueState(ahead, old.quantity, filled, old.seen_event_ids+(event_id,), state)

    @staticmethod
    def evidence(state: QueueState) -> dict[str, Any]:
        return {"state": state.state, "filled": state.filled,
                "evidence_class": "MODELLED_NOT_E3", "qualifies": False,
                "event_ids": state.seen_event_ids}
