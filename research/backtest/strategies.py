"""
research/backtest/strategies.py
===============================
Standardized strategy adapter layer for Track 2 backtesting and prospective observation.
Each adapter wraps strategy evaluation inside a StrategyContext that guarantees zero lookahead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from research.backtest.bars import Bar, CandleStore, DailyBar


class LookAheadError(RuntimeError):
    """Raised when strategy code asks for data that was not available at the decision time."""


class PointInTimeView:
    """Read-only view of a CandleStore as of one decision (plan D17).

    Only sessions strictly before the current one are reachable. Bars of the current session reach
    strategies through StrategyContext.bars, which the engine cuts at the decision time.
    """

    def __init__(self, store: CandleStore, session: date, decision_time: datetime) -> None:
        from research.data.holdout import assert_not_qa

        assert_not_qa(store, "PointInTimeView")
        self._store = store
        self.session = session
        self.decision_time = decision_time

    @property
    def symbols(self) -> List[str]:
        return self._store.symbols

    def sessions(self, symbol: str) -> List[date]:
        return [d for d in self._store.sessions(symbol) if d < self.session]

    def bars(self, symbol: str, day: date) -> List[Bar]:
        if day >= self.session:
            raise LookAheadError(f"bars for {symbol} on {day} are not closed at {self.decision_time}")
        return self._store.bars(symbol, day)

    def daily_before(self, symbol: str, day: date) -> List[DailyBar]:
        if day > self.session:
            raise LookAheadError(f"daily bars before {day} include sessions after {self.session}")
        return self._store.daily_before(symbol, day)

    def daily(self, symbol: str) -> List[DailyBar]:
        return self._store.daily_before(symbol, self.session)


@dataclass(frozen=True)
class StrategyContext:
    symbol: str
    decision_time: datetime
    current: Bar
    bars: Sequence[Bar]
    daily_bars: Sequence[DailyBar] = field(default_factory=list)
    nifty_bars: Sequence[Bar] = field(default_factory=list)
    peers: Mapping[str, Sequence[Bar]] = field(default_factory=dict)
    sector: str = ""
    store: Optional[Any] = None                  # PointInTimeView in the engine (plan D17), never a raw store
    # plan D13: richer point-in-time inputs for new adapters
    index_bars: Mapping[str, Sequence[Bar]] = field(default_factory=dict)
    calibration: Optional[Any] = None
    events: Optional[Any] = None
    bar_index: int = -1


@dataclass
class SignalIntent:
    strategy: str
    symbol: str
    side: str
    stop_loss: float
    targets: Sequence[Tuple[float, float]]
    max_bars: Optional[int] = None
    is_shadow: bool = False
    signal_time: Optional[datetime] = None
    entry_ref: float = 0.0
    disposition: str = "PENDING"
    counterfactual_net_r: Optional[float] = None
    # plan D13
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    priority_score: float = 0.0
    r_basis: Optional[str] = None
    # counterfactual outcome from the shared per-signal simulation (plan D8, D9, D15)
    counterfactual_exit_reason: str = ""
    counterfactual_evidence_class: str = "E1_CF"
    counterfactual_fee_estimated: bool = False
    # net = gross - fee - slip (signal_sim); kept so a study can say whether a loss is the signal or its cost
    counterfactual_gross_r: Optional[float] = None
    counterfactual_fee_r: Optional[float] = None
    counterfactual_slip_r: Optional[float] = None
    qty_planned: int = 0
    trade_id: Optional[str] = None

    @classmethod
    def from_ctx(
        cls,
        ctx: StrategyContext,
        strategy: str,
        side: str,
        stop: float,
        targets: Sequence[Tuple[float, float]],
        max_bars: Optional[int] = None,
        is_shadow: bool = False,
    ) -> SignalIntent:
        return cls(
            strategy=strategy,
            symbol=ctx.symbol,
            side=side.upper(),
            stop_loss=float(stop),
            targets=tuple((float(p), float(w)) for p, w in targets),
            max_bars=max_bars,
            is_shadow=is_shadow,
            signal_time=ctx.decision_time,
            entry_ref=float(ctx.current.close),
        )


@dataclass(frozen=True)
class Decision:
    strategy: str
    symbol: str
    action: str  # "SIGNAL", "NO_PATTERN", "DATA_INVALID"
    intent: Optional[SignalIntent] = None
    reason: str = ""


class StrategyAdapter:
    """Base interface for all strategy backtest adapters."""
    name: str = "BASE"
    is_shadow: bool = False

    def evaluate(self, ctx: StrategyContext) -> Decision:
        raise NotImplementedError


class OrbAdapter(StrategyAdapter):
    """ORB_SIMPLE (plan P5.3): the simplified 15-minute ORB kept for comparison only. It is NOT the
    production rule set; the production-equivalent baseline is research/strategies/orb_prod.py (ORB_PROD).
    Renamed from ORB_MOMENTUM so no report can confuse the two."""
    name: str = "ORB_SIMPLE"

    def __init__(self, is_shadow: bool = False, min_baseline_bars: int = 20) -> None:
        self.is_shadow = is_shadow
        self.min_baseline_bars = min_baseline_bars

    def evaluate(self, ctx: StrategyContext) -> Decision:
        # Fails closed without sufficient baseline daily bars or 20-day volume history
        if len(ctx.daily_bars) < self.min_baseline_bars:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        
        # Need at least the 09:15 opening bar to have completed
        if len(ctx.bars) < 2:
            return Decision(self.name, ctx.symbol, "NO_PATTERN")
        
        # Intraday ORB evaluation
        orb_bar = ctx.bars[0]
        curr = ctx.current
        if curr.close > orb_bar.high:
            stop = orb_bar.low
            risk = curr.close - stop
            if risk <= 0:
                return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="INVERTED_STOP")
            targets = ((round(curr.close + 1.5 * risk, 2), 0.5), (round(curr.close + 3.0 * risk, 2), 0.5))
            intent = SignalIntent.from_ctx(ctx, self.name, "BUY", stop, targets, is_shadow=self.is_shadow)
            return Decision(self.name, ctx.symbol, "SIGNAL", intent)
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


class VwapReclaimAdapter(StrategyAdapter):
    name: str = "VWAP_RECLAIM"

    def __init__(self, is_shadow: bool = True) -> None:
        self.is_shadow = is_shadow

    def evaluate(self, ctx: StrategyContext) -> Decision:
        if len(ctx.daily_bars) < 20:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


class VolSqueezeAdapter(StrategyAdapter):
    name: str = "VOL_SQUEEZE"

    def __init__(self, is_shadow: bool = True) -> None:
        self.is_shadow = is_shadow

    def evaluate(self, ctx: StrategyContext) -> Decision:
        if len(ctx.daily_bars) < 20:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


class TrapdoorAdapter(StrategyAdapter):
    name: str = "TRAPDOOR"

    def __init__(self, is_shadow: bool = True) -> None:
        self.is_shadow = is_shadow

    def evaluate(self, ctx: StrategyContext) -> Decision:
        if len(ctx.daily_bars) < 20:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


class LastLightAdapter(StrategyAdapter):
    name: str = "LAST_LIGHT"

    def __init__(self, is_shadow: bool = True) -> None:
        self.is_shadow = is_shadow

    def evaluate(self, ctx: StrategyContext) -> Decision:
        if len(ctx.daily_bars) < 20:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


class RecoilAdapter(StrategyAdapter):
    name: str = "RECOIL"

    def __init__(self, is_shadow: bool = True) -> None:
        self.is_shadow = is_shadow

    def evaluate(self, ctx: StrategyContext) -> Decision:
        if len(ctx.daily_bars) < 20:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


class CompassAdapter(StrategyAdapter):
    name: str = "COMPASS"

    def __init__(self, is_shadow: bool = True) -> None:
        self.is_shadow = is_shadow

    def evaluate(self, ctx: StrategyContext) -> Decision:
        if len(ctx.daily_bars) < 20:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


class PeadAdapter(StrategyAdapter):
    """
    S1: Results-Day Post-Earnings Announcement Drift (PEAD) Sleeve.
    Long-only CNC (optional MIS short day 0).
    Structural stop 3-6% below Day-0 low; position sized down when stop > 2.57% to keep risk at Rs 1,500.
    """
    name: str = "PEAD_DRIFT"

    def __init__(self, is_shadow: bool = True) -> None:
        self.is_shadow = is_shadow

    def evaluate(self, ctx: StrategyContext) -> Decision:
        if len(ctx.daily_bars) < 20:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


class SweepReclaimAdapter(StrategyAdapter):
    """
    S2: Liquidity-Shock Reversal (Sweep-and-Reclaim) with 5-Level Depth Skew Filter.
    MIS intraday both sides.
    Triggers on liquidity sweeps through prior-day low or opening range low (>= 0.15 ATR15)
    with same-bar or next-bar reclaim, volume > 2x slot median, close location >= 0.60.
    """
    name: str = "SWEEP_RECLAIM"

    def __init__(self, is_shadow: bool = True) -> None:
        self.is_shadow = is_shadow

    def evaluate(self, ctx: StrategyContext) -> Decision:
        if len(ctx.daily_bars) < 20:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


class LateMomentumAdapter(StrategyAdapter):
    """
    S3: Late-Session Market-Momentum Continuation (High-Beta Constituents).
    MIS intraday both sides.
    Evaluates market trend alignment between 09:45 and 14:15, selecting top beta stocks
    closing beyond session VWAP. Entries at 14:15-14:30 bar close, hard flat 15:10 (q ≈ 0).
    """
    name: str = "LATE_MOMENTUM"

    def __init__(self, is_shadow: bool = True) -> None:
        self.is_shadow = is_shadow

    def evaluate(self, ctx: StrategyContext) -> Decision:
        if len(ctx.daily_bars) < 20:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


class CasReversalAdapter(StrategyAdapter):
    """
    S4: Closing Auction Session (CAS) Dislocation Overnight Reversal.
    Long-only CNC.
    Dislocation D = (Indicative 15:27 - LTP 15:15) / LTP 15:15 <= -0.60% with sell imbalance.
    Exits at next morning pre-open / 09:30 or 10:15, clearing capital for intraday slots.
    """
    name: str = "CAS_REVERSAL"

    def __init__(self, is_shadow: bool = True) -> None:
        self.is_shadow = is_shadow

    def evaluate(self, ctx: StrategyContext) -> Decision:
        if len(ctx.daily_bars) < 20:
            return Decision(self.name, ctx.symbol, "DATA_INVALID", reason="MISSING_BASELINE")
        return Decision(self.name, ctx.symbol, "NO_PATTERN")


OrbSimpleAdapter = OrbAdapter


def default_adapters() -> List[StrategyAdapter]:
    """Default adapter lineup: ORB active, remaining candidate strategies in shadow mode."""
    return [
        OrbAdapter(is_shadow=False),
        VwapReclaimAdapter(is_shadow=True),
        VolSqueezeAdapter(is_shadow=True),
        TrapdoorAdapter(is_shadow=True),
        LastLightAdapter(is_shadow=True),
        RecoilAdapter(is_shadow=True),
        CompassAdapter(is_shadow=True),
    ]


def incubated_slate_adapters() -> List[StrategyAdapter]:
    """Ranked, evidence-based incubation slate per 24-Sep audit memo (S1 to S4)."""
    return [
        PeadAdapter(is_shadow=True),
        SweepReclaimAdapter(is_shadow=True),
        LateMomentumAdapter(is_shadow=True),
        CasReversalAdapter(is_shadow=True),
    ]

