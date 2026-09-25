"""
research/backtest/engine.py
===========================
Event-driven backtest engine for Track 2 (research only, paper only).

Per session, at every bar close:
  1. positions whose exit bar has finished release their slot;
  2. if the session policy allows entries, every eligible TRADABLE symbol (never an INDEX series,
     plan D5) is evaluated by every adapter, with point-in-time inputs only (plan D17);
  3. all intents of that bar close go to the Allocator (plan D6), then through the checks below.

Every intent is simulated by research/studies/signal_sim.simulate_signal, whatever its disposition
(plan D15). Allocated trades are evidence class E1 and counterfactuals E1_CF (plan D8); neither is ever
admissible to the locked gate.

Checks, in order (dispositions):
  REJECTED_GOVERNOR_MISSING_MARGIN_RATE   var_elm_rate is None (study runs must set it, plan D2)
  REJECTED_GOVERNOR_INVERTED_STOP         a SELL while allow_shorts is False, or a stop on the wrong side
  BLOCKED_PENDING                         the symbol already has an open or just-approved position (D16)
  BLOCKED_REENTRY                         the symbol already traded this session
  SHADOW_NOT_ALLOCATED                    shadow intent while allow_shadow is False
  REJECTED_GOVERNOR_MAX_SLOTS / _SECTOR_LIMIT / _CLUSTER_LIMIT   (cluster cap only with a clusters map, D7)
  ZERO_QTY                                sizing gives 0 shares (D16)
  MISSED_CLAMP / NO_NEXT_BAR / NO_LIQUIDITY / INVALID_RISK   from the simulation
  ALLOCATED                               set only after a fill (D16)

Defaults preserve the previous engine's behaviour. Study configs must set the research options
explicitly: allow_shorts, stop_limit_offset_pct=0.005, r_basis="stop_limit", var_elm_rate.
"""
from __future__ import annotations

import bisect
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from research.backtest.bars import IST, Bar, CandleStore, round_to_tick, tick_size
from research.backtest.cost_model import ProductType
from research.backtest.policy import SessionPolicy
from research.backtest.strategies import PointInTimeView, SignalIntent, StrategyAdapter, StrategyContext
from research.backtest.universe import PointInTimeUniverse
from research.data.indices import canonical_index
from research.decision.allocator import Allocator, DefaultAllocator
from research.decision.sizing import AGGREGATE_EXPOSURE_CAP_RS, MAX_SLOTS, RISK_BUDGET_RS, SLOT_CAP_RS
from research.studies.signal_sim import SimConfig, SimResult, side_sign, simulate_signal, size_qty


@dataclass(frozen=True)
class EngineConfig:
    corpus_rs: float = 250_000.0
    # Adjusted A1 (Yashu, 25 Sep 2026; single source research/decision/sizing.py). Was 58,333.33 per slot.
    risk_budget_rs: float = RISK_BUDGET_RS                        # Rs 1,500 maximum planned risk per trade
    max_slots: int = MAX_SLOTS                                    # 3
    slot_cap_rs: float = SLOT_CAP_RS                              # Rs 38,000 per position
    aggregate_exposure_cap_rs: float = AGGREGATE_EXPOSURE_CAP_RS  # Rs 1,14,000, filled + pending, absolute
    # The slot cap is applied at the worst admissible entry price (entry_ref x (1 + clamp) + entry ticks,
    # rounded up), so a filled position never exceeds it and three full positions fit under the aggregate
    # cap. False applies it at entry_ref (the pre-A1 behaviour, kept for the audit replays).
    slot_cap_on_worst_entry: bool = True
    max_positions_per_sector: int = 2
    var_elm_rate: Optional[float] = None
    allow_shadow: bool = False
    clamp_pct: float = 0.0010  # 10 bps default entry clamp
    policy: SessionPolicy = field(default_factory=SessionPolicy)
    # plan P2 options (defaults keep the previous behaviour)
    allow_shorts: bool = False                    # D1
    entry_mode: str = "next_open"                 # D19: or "signal_close"
    entry_slippage_ticks: int = 1
    stop_slippage_ticks: int = 1                  # D3: k
    exit_slippage_ticks: int = 1
    stop_limit_offset_pct: Optional[float] = None  # D3: 0.005 = production SL-limit offset; None = SL-M
    r_basis: str = "trigger"                      # D3: or "stop_limit"
    cost_mode: str = "dhan"                       # D19: or "flat_pct_of_entry_notional"
    flat_cost_pct: float = 0.00106
    max_per_cluster: int = 1                      # D7 (applies only when a clusters map is supplied)
    allocation_seed: int = 0                      # D6
    allow_reentry_same_day: bool = False
    product: ProductType = ProductType.MIS

    def sim_config(self) -> SimConfig:
        return SimConfig(entry_mode=self.entry_mode, entry_slippage_ticks=self.entry_slippage_ticks,
                         clamp_pct=self.clamp_pct, stop_slippage_ticks=self.stop_slippage_ticks,
                         exit_slippage_ticks=self.exit_slippage_ticks,
                         stop_limit_offset_pct=self.stop_limit_offset_pct, r_basis=self.r_basis,
                         cost_mode=self.cost_mode, flat_cost_pct=self.flat_cost_pct, product=self.product,
                         policy=self.policy)


@dataclass(frozen=True)
class ExitRecord:
    reason: str
    price: float
    qty: int
    time: datetime
    ideal_price: float = math.nan
    method: str = ""


@dataclass
class Trade:
    symbol: str
    strategy: str
    entry_time: datetime
    entry_price: float
    qty: int
    stop_loss: float
    targets: Sequence[Tuple[float, float]]
    intent: SignalIntent
    session: date
    exits: List[ExitRecord] = field(default_factory=list)
    exit_reason: str = ""
    gross_pnl: float = 0.0
    charges: float = 0.0
    rms_fee: float = 0.0
    net_pnl: float = 0.0
    net_r: float = math.nan
    evidence_method: str = "BAR_MODEL"
    evidence_class: str = "E1"
    side: str = "BUY"
    trade_id: str = ""
    stop_limit: Optional[float] = None
    r_basis: str = "trigger"
    risk_rs: float = math.nan
    gross_r: float = math.nan
    fee_r: float = math.nan
    slip_r: float = math.nan
    slippage_rs: float = 0.0
    be_reference: Optional[str] = None
    gap_through_limit: bool = False
    holding_bars: int = 0
    release_time: Optional[datetime] = None
    reserved_notional_rs: float = math.nan       # Adjusted A1: exposure booked before the entry fills


@dataclass
class EngineResult:
    trades: List[Trade]
    signals: List[SignalIntent]
    decision_counts: Dict[str, Dict[str, int]]
    daily_pnl: Dict[date, float]
    daily_returns: List[float]
    dates: List[date]
    per_strategy_daily_pnl: Dict[str, Dict[date, float]] = field(default_factory=dict)      # D11
    per_strategy_daily_cf_r: Dict[str, Dict[date, float]] = field(default_factory=dict)     # D11
    disposition_counts: Dict[str, int] = field(default_factory=dict)                        # D2
    rms_exits: int = 0                                                                      # D10
    gap_through_limit_exits: int = 0                                                        # D3


def _trade_from_sim(intent: SignalIntent, sim: SimResult, session: date, trade_id: str) -> Trade:
    exits = [ExitRecord(e.reason, e.price, e.qty, e.time, e.ideal_price, e.method) for e in sim.exits]
    return Trade(symbol=intent.symbol, strategy=intent.strategy, entry_time=sim.entry_time,
                 entry_price=sim.entry_price, qty=sim.qty, stop_loss=intent.stop_loss, targets=intent.targets,
                 intent=intent, session=session, exits=exits, exit_reason=sim.exit_reason,
                 gross_pnl=sim.gross_pnl_rs, charges=sim.fees_rs, rms_fee=sim.rms_fee_rs, net_pnl=sim.net_pnl_rs,
                 net_r=sim.net_r, evidence_method="+".join(sorted({e.method for e in sim.exits})) or "BAR_MODEL",
                 evidence_class="E1", side=sim.side, trade_id=trade_id, stop_limit=sim.stop_limit,
                 r_basis=sim.r_basis, risk_rs=sim.risk_rs, gross_r=sim.gross_r, fee_r=sim.fee_r,
                 slip_r=sim.slip_r, slippage_rs=sim.slippage_rs, be_reference=sim.be_reference,
                 gap_through_limit=sim.gap_through_limit, holding_bars=sim.holding_bars,
                 release_time=sim.release_time)


class BacktestEngine:
    def __init__(
        self,
        store: CandleStore,
        universe: PointInTimeUniverse,
        adapters: Sequence[StrategyAdapter],
        config: Optional[EngineConfig] = None,
        sectors: Optional[Mapping[str, str]] = None,
        clusters: Optional[Mapping[date, Mapping[str, str]]] = None,
        allocator: Optional[Allocator] = None,
        calibration_provider: Optional[Any] = None,
        events_provider: Optional[Any] = None,
    ) -> None:
        from research.data.holdout import assert_not_qa

        assert_not_qa(store, "BacktestEngine")      # plan P3.7: QA data never reaches strategy code
        self.store = store
        self.universe = universe
        self.adapters = list(adapters)
        self.config = config or EngineConfig()
        self.sectors = dict(sectors or {})
        self.clusters = clusters
        self.policy = self.config.policy
        self.allocator = allocator or DefaultAllocator(seed=self.config.allocation_seed)
        # plan P4: point-in-time calibration (research/features/calibration.py) and events
        # (research/features/events.py) reach adapters through StrategyContext
        self.calibration_provider = calibration_provider
        self.events_provider = events_provider
        self._sim_cfg = self.config.sim_config()

    # ------------------------------------------------------------------ run
    def run(self) -> EngineResult:
        symbols = self.store.symbols
        tradables = [s for s in symbols if self.store.kind(s) == "TRADABLE"]
        indices = [s for s in symbols if self.store.kind(s) == "INDEX"]
        session_sets = {s: set(self.store.sessions(s)) for s in symbols}
        all_dates = sorted({d for s in tradables for d in session_sets[s]})

        trades: List[Trade] = []
        signals: List[SignalIntent] = []
        counts: Dict[str, Dict[str, int]] = {a.name: defaultdict(int) for a in self.adapters}
        for day in all_dates:
            trades.extend(self._run_session(day, tradables, indices, session_sets, signals, counts))

        pnl = {d: 0.0 for d in all_dates}
        per_pnl: Dict[str, Dict[date, float]] = {a.name: {d: 0.0 for d in all_dates} for a in self.adapters}
        per_cf: Dict[str, Dict[date, float]] = {a.name: {d: 0.0 for d in all_dates} for a in self.adapters}
        for t in trades:
            pnl[t.session] += t.net_pnl
            per_pnl.setdefault(t.strategy, {d: 0.0 for d in all_dates})[t.session] += t.net_pnl
        for s in signals:
            r = s.counterfactual_net_r
            if r is not None and math.isfinite(r) and s.signal_time is not None:
                day = s.signal_time.astimezone(IST).date()
                per_cf.setdefault(s.strategy, {d: 0.0 for d in all_dates})[day] += r
        return EngineResult(
            trades=trades, signals=signals, decision_counts={k: dict(v) for k, v in counts.items()},
            daily_pnl=pnl, daily_returns=[pnl[d] / self.config.corpus_rs for d in all_dates], dates=all_dates,
            per_strategy_daily_pnl=per_pnl, per_strategy_daily_cf_r=per_cf,
            disposition_counts=dict(Counter(s.disposition for s in signals)),
            rms_exits=sum(1 for t in trades if t.exit_reason == "RMS_SQUAREOFF"),
            gap_through_limit_exits=sum(1 for t in trades if t.gap_through_limit))

    # ------------------------------------------------------------------ one session
    def _run_session(self, day: date, tradables: Sequence[str], indices: Sequence[str],
                     session_sets: Mapping[str, Set[date]], signals: List[SignalIntent],
                     counts: Dict[str, Dict[str, int]]) -> List[Trade]:
        cfg = self.config
        bars_by = {s: self.store.bars(s, day) for s in tradables if day in session_sets[s]}
        bars_by = {s: b for s, b in bars_by.items() if b}
        eligible = sorted(s for s, b in bars_by.items() if self.universe.check(s, day, price=b[0].open)[0])
        if not eligible:
            return []
        index_by: Dict[str, List[Bar]] = {}
        for s in indices:
            if day in session_sets[s]:
                index_by[canonical_index(s) or s] = self.store.bars(s, day)
        index_ends = {n: [b.end for b in v] for n, v in index_by.items()}
        ends_by = {s: [b.end for b in bars_by[s]] for s in eligible}
        start_idx = {s: {b.start: i for i, b in enumerate(bars_by[s])} for s in eligible}
        daily_cache = {s: self.store.daily_before(s, day) for s in eligible}
        by_sector: Dict[str, List[str]] = defaultdict(list)
        for s in eligible:
            if self.sectors.get(s):
                by_sector[self.sectors[s]].append(s)
        day_clusters = (self.clusters or {}).get(day, {})
        session_cal = self.calibration_provider.session(day) if self.calibration_provider is not None else None

        active: List[Trade] = []
        session_trades: List[Trade] = []
        traded_today: Set[str] = set()
        for t_start in sorted({b.start for s in eligible for b in bars_by[s]}):
            sample = next(bars_by[s][start_idx[s][t_start]] for s in eligible if t_start in start_idx[s])
            decision_time = sample.end
            active = [t for t in active if t.release_time is None or t.release_time > decision_time]
            if not self.policy.entries_allowed(decision_time.astimezone(IST).time()):
                continue
            view = PointInTimeView(self.store, day, decision_time)
            idx_now = {n: v[:bisect.bisect_right(index_ends[n], decision_time)] for n, v in index_by.items()}
            nifty_now = idx_now.get("IDX:NIFTY50", [])
            candidates = []
            for s in eligible:
                i = start_idx[s].get(t_start)
                if i is None:
                    continue
                sector = self.sectors.get(s, "")
                peers = {p: bars_by[p][:bisect.bisect_right(ends_by[p], decision_time)]
                         for p in by_sector.get(sector, []) if p != s} if sector else {}
                ctx = StrategyContext(symbol=s, decision_time=decision_time, current=bars_by[s][i],
                                      bars=bars_by[s][: i + 1], daily_bars=daily_cache[s], nifty_bars=nifty_now,
                                      peers=peers, sector=sector, store=view, index_bars=idx_now, bar_index=i,
                                      calibration=session_cal, events=self.events_provider)
                for order, adapter in enumerate(self.adapters):
                    decision = adapter.evaluate(ctx)
                    counts[adapter.name][decision.action] += 1
                    if decision.action != "SIGNAL" or decision.intent is None:
                        continue
                    intent = decision.intent
                    if intent.r_basis is None:
                        intent.r_basis = cfg.r_basis
                    signals.append(intent)
                    candidates.append((order, s, i, intent))
            for _order, s, i, intent in self.allocator.rank(candidates, day):
                trade = self._handle(intent, bars_by[s], i, day, active, traded_today, day_clusters,
                                     len(session_trades) + 1)
                if trade is not None:
                    active.append(trade)
                    session_trades.append(trade)
                    traded_today.add(intent.symbol)
        return session_trades

    # ------------------------------------------------------------------ one intent
    def _handle(self, intent: SignalIntent, bars: Sequence[Bar], i: int, day: date, active: List[Trade],
                traded_today: Set[str], day_clusters: Mapping[str, str], seq: int) -> Optional[Trade]:
        cfg = self.config
        try:
            sg = side_sign(intent.side)
        except ValueError:
            intent.disposition = "REJECTED_INVALID_SIDE"
            return None
        cap_px = None
        if cfg.slot_cap_on_worst_entry and intent.entry_ref > 0 and math.isfinite(intent.entry_ref):
            # worst admissible entry: the clamp plus the entry slippage ticks (Adjusted A1 slot cap)
            worst = intent.entry_ref * (1 + cfg.clamp_pct)
            cap_px = round_to_tick(worst, "up") + cfg.entry_slippage_ticks * tick_size(worst)
        qty = size_qty(intent.entry_ref, intent.stop_loss, intent.side, cfg.risk_budget_rs, cfg.slot_cap_rs,
                       cfg.stop_limit_offset_pct, notional_px=cap_px)
        intent.qty_planned = qty
        reserve = qty * (cap_px if cap_px is not None else intent.entry_ref)
        decided_at = bars[i].end
        # absolute notional booked now: filled trades at their fill notional, trades whose entry has not
        # filled by this decision at their reservation (Adjusted A1 aggregate cap)
        # strict '<': an entry at the next bar's open (== this bar's end) has not filled at this decision
        exposure = sum(abs(t.entry_price * t.qty) if t.entry_time < decided_at else t.reserved_notional_rs
                       for t in active)
        sim = simulate_signal(bars, i, intent.side, intent.entry_ref, intent.stop_loss, intent.targets,
                              intent.max_bars, qty, self._sim_cfg, day)
        intent.counterfactual_net_r = sim.net_r
        intent.counterfactual_exit_reason = sim.exit_reason or sim.disposition
        intent.counterfactual_fee_estimated = sim.fee_estimated
        intent.counterfactual_gross_r, intent.counterfactual_fee_r, intent.counterfactual_slip_r = \
            sim.gross_r, sim.fee_r, sim.slip_r
        intent.counterfactual_evidence_class = "E1_CF"

        sector = self.sectors.get(intent.symbol, "")
        cluster = day_clusters.get(intent.symbol)
        if cfg.var_elm_rate is None:
            intent.disposition = "REJECTED_GOVERNOR_MISSING_MARGIN_RATE"
        elif sg < 0 and not cfg.allow_shorts:
            intent.disposition = "REJECTED_GOVERNOR_INVERTED_STOP"
        elif sg * (intent.entry_ref - intent.stop_loss) <= 0:
            intent.disposition = "REJECTED_GOVERNOR_INVERTED_STOP"
        elif any(t.symbol == intent.symbol for t in active):
            intent.disposition = "BLOCKED_PENDING"
        elif intent.symbol in traded_today and not cfg.allow_reentry_same_day:
            intent.disposition = "BLOCKED_REENTRY"
        elif intent.is_shadow and not cfg.allow_shadow:
            intent.disposition = "SHADOW_NOT_ALLOCATED"
        elif len(active) >= cfg.max_slots:
            intent.disposition = "REJECTED_GOVERNOR_MAX_SLOTS"
        elif sector and sum(1 for t in active if self.sectors.get(t.symbol) == sector) >= cfg.max_positions_per_sector:
            intent.disposition = "REJECTED_GOVERNOR_SECTOR_LIMIT"
        elif cluster is not None and sum(1 for t in active if day_clusters.get(t.symbol) == cluster) >= cfg.max_per_cluster:
            intent.disposition = "REJECTED_GOVERNOR_CLUSTER_LIMIT"
        elif qty <= 0:
            intent.disposition = "ZERO_QTY"
        elif not (exposure + reserve <= cfg.aggregate_exposure_cap_rs + 1e-6):
            intent.disposition = "REJECTED_GOVERNOR_AGGREGATE_EXPOSURE_CAP"
        elif not sim.filled:
            intent.disposition = sim.disposition
        else:
            intent.disposition = "ALLOCATED"
            trade_id = f"{day.isoformat()}-{intent.symbol}-{intent.strategy}-{seq}"
            intent.trade_id = trade_id
            trade = _trade_from_sim(intent, sim, day, trade_id)
            trade.reserved_notional_rs = reserve
            return trade
        return None
