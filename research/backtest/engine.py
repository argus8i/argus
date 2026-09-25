"""
research/backtest/engine.py
===========================
Event-driven backtesting engine for Track 2 liquid short-term momentum.
Enforces realistic discrete execution, single session policy clock, per-order fee schedule,
fail-closed governor gates, and counterfactual tracking for rejected/shadow signals.
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

from research.backtest.bars import IST, Bar, CandleStore, round_to_tick, tick_size
from research.backtest.cost_model import DhanFeeEngine, OrderSide, ProductType
from research.backtest.policy import SessionPolicy
from research.backtest.strategies import Decision, SignalIntent, StrategyAdapter, StrategyContext
from research.backtest.universe import PointInTimeUniverse


@dataclass(frozen=True)
class EngineConfig:
    corpus_rs: float = 250_000.0
    risk_budget_rs: float = 1500.0
    max_slots: int = 3
    slot_cap_rs: float = 58333.33  # 250,000 * 0.70 / 3
    max_positions_per_sector: int = 2
    var_elm_rate: Optional[float] = None
    allow_shadow: bool = False
    clamp_pct: float = 0.0010  # 10 bps default entry clamp
    policy: SessionPolicy = field(default_factory=SessionPolicy)


@dataclass(frozen=True)
class ExitRecord:
    reason: str
    price: float
    qty: int
    time: datetime


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
    net_r: float = 0.0
    evidence_method: str = "BAR_MODEL"
    evidence_class: str = "BAR_MODEL"

    # Runtime tracking
    current_stop: float = 0.0
    remaining_qty: int = 0
    is_breakeven: bool = False
    bars_held: int = 0
    target_idx: int = 0
    is_closed: bool = False

    def __post_init__(self) -> None:
        self.current_stop = self.stop_loss
        self.remaining_qty = self.qty


@dataclass
class EngineResult:
    trades: List[Trade]
    signals: List[SignalIntent]
    decision_counts: Dict[str, Dict[str, int]]
    daily_pnl: Dict[date, float]
    daily_returns: List[float]
    dates: List[date]


class BacktestEngine:
    def __init__(
        self,
        store: CandleStore,
        universe: PointInTimeUniverse,
        adapters: Sequence[StrategyAdapter],
        config: Optional[EngineConfig] = None,
        sectors: Optional[Mapping[str, str]] = None,
    ) -> None:
        self.store = store
        self.universe = universe
        self.adapters = list(adapters)
        self.config = config or EngineConfig()
        self.sectors = dict(sectors or {})
        self.policy = self.config.policy

    def run(self) -> EngineResult:
        all_symbols = self.store.symbols
        all_dates = sorted({d for s in all_symbols for d in self.store.sessions(s)})

        completed_trades: List[Trade] = []
        all_signals: List[SignalIntent] = []
        decision_counts: Dict[str, Dict[str, int]] = {
            a.name: defaultdict(int) for a in self.adapters
        }

        for session_date in all_dates:
            daily_trades = self._run_session(
                session_date=session_date,
                all_signals=all_signals,
                decision_counts=decision_counts,
            )
            completed_trades.extend(daily_trades)

        # Aggregate daily PnL and returns
        pnl_by_date: Dict[date, float] = {d: 0.0 for d in all_dates}
        for t in completed_trades:
            pnl_by_date[t.session] = pnl_by_date.get(t.session, 0.0) + t.net_pnl

        daily_returns: List[float] = [
            pnl_by_date[d] / self.config.corpus_rs for d in all_dates
        ]

        return EngineResult(
            trades=completed_trades,
            signals=all_signals,
            decision_counts={k: dict(v) for k, v in decision_counts.items()},
            daily_pnl=pnl_by_date,
            daily_returns=daily_returns,
            dates=all_dates,
        )

    def _run_session(
        self,
        session_date: date,
        all_signals: List[SignalIntent],
        decision_counts: Dict[str, Dict[str, int]],
    ) -> List[Trade]:
        symbols_today = [
            s for s in self.store.symbols if session_date in self.store.sessions(s)
        ]
        if not symbols_today:
            return []

        bars_by_sym: Dict[str, List[Bar]] = {
            s: self.store.bars(s, session_date) for s in symbols_today
        }

        all_starts = sorted({b.start for bars in bars_by_sym.values() for b in bars})

        active_trades: List[Trade] = []
        session_completed_trades: List[Trade] = []
        traded_today: Set[str] = set()
        pending_orders: List[SignalIntent] = []

        nifty_series = bars_by_sym.get("NIFTY50", [])

        for idx, bar_start in enumerate(all_starts):
            # Map of symbols having a bar starting at bar_start
            current_bars = {
                s: next((b for b in bars if b.start == bar_start), None)
                for s, bars in bars_by_sym.items()
            }
            sample_bar = next((b for b in current_bars.values() if b is not None), None)
            if sample_bar is None:
                continue
            current_bar_end = sample_bar.end

            # 1. Process entry for pending orders at the open of this bar
            if pending_orders:
                orders_to_process = list(pending_orders)
                pending_orders.clear()
                for intent in orders_to_process:
                    curr_bar = current_bars.get(intent.symbol)
                    if curr_bar is None:
                        continue

                    # Clamp check: entry open must not gap beyond clamp threshold
                    max_open = intent.entry_ref * (1.0 + self.config.clamp_pct)
                    if curr_bar.open > max_open + 1e-6:
                        intent.disposition = "MISSED_CLAMP"
                        continue

                    intent.disposition = "ALLOCATED"
                    entry_px = round_to_tick(curr_bar.open + tick_size(curr_bar.open))
                    per_share_risk = intent.entry_ref - intent.stop_loss
                    if per_share_risk <= 0:
                        continue

                    qty_risk = int(self.config.risk_budget_rs // per_share_risk)
                    qty_slot = int(self.config.slot_cap_rs // intent.entry_ref)
                    qty = min(qty_risk, qty_slot)
                    if qty <= 0:
                        continue

                    trade = Trade(
                        symbol=intent.symbol,
                        strategy=intent.strategy,
                        entry_time=curr_bar.start,
                        entry_price=entry_px,
                        qty=qty,
                        stop_loss=intent.stop_loss,
                        targets=intent.targets,
                        intent=intent,
                        session=session_date,
                    )
                    active_trades.append(trade)
                    traded_today.add(intent.symbol)

            # 2. Update active positions on current bar
            remaining_active: List[Trade] = []
            for trade in active_trades:
                curr_bar = current_bars.get(trade.symbol)
                if curr_bar is None:
                    remaining_active.append(trade)
                    continue

                self._process_bar_for_trade(
                    trade=trade,
                    bar=curr_bar,
                    session_completed_trades=session_completed_trades,
                )
                if not trade.is_closed:
                    remaining_active.append(trade)
            active_trades = remaining_active

            # 3. Strategy evaluation at the CLOSE of this bar
            decision_time = current_bar_end
            if decision_time.astimezone(IST).time() >= self.policy.freeze_entries:
                continue

            for sym in symbols_today:
                if sym == "NIFTY50":
                    continue
                bars = bars_by_sym[sym]
                bar_up_to = [b for b in bars if b.end <= decision_time]
                if not bar_up_to:
                    continue
                current_bar = bar_up_to[-1]
                if current_bar.end != decision_time:
                    continue

                # Point-in-time universe check
                eligible, u_reason = self.universe.check(
                    sym, session_date, price=current_bar.close
                )
                if not eligible:
                    continue

                # Sector peers
                sector = self.sectors.get(sym, "")
                peers: Dict[str, List[Bar]] = {}
                if sector:
                    for peer_sym in symbols_today:
                        if peer_sym != sym and self.sectors.get(peer_sym) == sector:
                            p_up = [b for b in bars_by_sym[peer_sym] if b.end <= decision_time]
                            if p_up:
                                peers[peer_sym] = p_up

                nifty_up_to = [b for b in nifty_series if b.end <= decision_time]
                daily_bars = self.store.daily_before(sym, session_date)

                ctx = StrategyContext(
                    symbol=sym,
                    decision_time=decision_time,
                    current=current_bar,
                    bars=bar_up_to,
                    daily_bars=daily_bars,
                    nifty_bars=nifty_up_to,
                    peers=peers,
                    sector=sector,
                    store=self.store,
                )

                for adapter in self.adapters:
                    decision = adapter.evaluate(ctx)
                    decision_counts[adapter.name][decision.action] += 1
                    if decision.action != "SIGNAL" or not decision.intent:
                        continue

                    intent = decision.intent
                    all_signals.append(intent)

                    approved, reason = self._assess_intent(
                        intent=intent,
                        session_date=session_date,
                        traded_today=traded_today,
                        active_trades=active_trades,
                        pending_orders=pending_orders,
                        sym_bars=bars,
                        current_bar=current_bar,
                    )

                    if approved:
                        pending_orders.append(intent)

        # 4. Handle any remaining open positions when session ends
        for trade in active_trades:
            last_bar = bars_by_sym[trade.symbol][-1]
            self._close_rms_squareoff(trade, last_bar)
            session_completed_trades.append(trade)

        return session_completed_trades

    def _assess_intent(
        self,
        intent: SignalIntent,
        session_date: date,
        traded_today: Set[str],
        active_trades: List[Trade],
        pending_orders: List[SignalIntent],
        sym_bars: List[Bar],
        current_bar: Bar,
    ) -> Tuple[bool, str]:
        # 1. Governor fail-closed checks
        if self.config.var_elm_rate is None:
            intent.disposition = "REJECTED_GOVERNOR_MISSING_MARGIN_RATE"
            self._simulate_counterfactual(intent, sym_bars, current_bar)
            return False, intent.disposition

        # Long-only desk: reject short intent
        if intent.side != "BUY":
            intent.disposition = "REJECTED_GOVERNOR_INVERTED_STOP"
            self._simulate_counterfactual(intent, sym_bars, current_bar)
            return False, intent.disposition

        # Inverted stop check
        if intent.stop_loss >= intent.entry_ref:
            intent.disposition = "REJECTED_GOVERNOR_INVERTED_STOP"
            self._simulate_counterfactual(intent, sym_bars, current_bar)
            return False, intent.disposition

        # No same-day re-entry
        if intent.symbol in traded_today:
            intent.disposition = "BLOCKED_REENTRY"
            return False, intent.disposition

        # Shadow filtering
        if intent.is_shadow and not self.config.allow_shadow:
            intent.disposition = "SHADOW_NOT_ALLOCATED"
            self._simulate_counterfactual(intent, sym_bars, current_bar)
            return False, intent.disposition

        # Slot capacity check
        total_open = len(active_trades) + len(pending_orders)
        if total_open >= self.config.max_slots:
            intent.disposition = "REJECTED_GOVERNOR_MAX_SLOTS"
            self._simulate_counterfactual(intent, sym_bars, current_bar)
            return False, intent.disposition

        # Sector cap check
        sector = self.sectors.get(intent.symbol, "")
        if sector:
            sector_open = sum(
                1 for t in active_trades if self.sectors.get(t.symbol) == sector
            ) + sum(
                1 for o in pending_orders if self.sectors.get(o.symbol) == sector
            )
            if sector_open >= self.config.max_positions_per_sector:
                intent.disposition = "REJECTED_GOVERNOR_SECTOR_LIMIT"
                self._simulate_counterfactual(intent, sym_bars, current_bar)
                return False, intent.disposition

        return True, "APPROVED"

    def _process_bar_for_trade(
        self,
        trade: Trade,
        bar: Bar,
        session_completed_trades: List[Trade],
    ) -> None:
        trade.bars_held += 1

        # 1. Policy Bounded Exit Check
        # First bar spanning bounded_exit (e.g. 15:00 <= 15:05 < 15:15)
        if bar.start.astimezone(IST).time() <= self.policy.bounded_exit < bar.end.astimezone(IST).time():
            exit_px = round_to_tick(bar.open - tick_size(bar.open))
            trade.exits.append(
                ExitRecord(reason="POLICY_EXIT", price=exit_px, qty=trade.remaining_qty, time=bar.start)
            )
            trade.remaining_qty = 0
            trade.exit_reason = "POLICY_EXIT"
            trade.is_closed = True
            self._finalize_trade(trade)
            session_completed_trades.append(trade)
            return

        # 2. Gap Stop Check at Open
        if bar.open <= trade.current_stop:
            exit_px = round_to_tick(bar.open - tick_size(bar.open))
            reason = "BREAKEVEN_STOP" if trade.is_breakeven else "STOP"
            trade.exits.append(
                ExitRecord(reason=reason, price=exit_px, qty=trade.remaining_qty, time=bar.start)
            )
            trade.remaining_qty = 0
            trade.exit_reason = reason
            trade.is_closed = True
            self._finalize_trade(trade)
            session_completed_trades.append(trade)
            return

        # 3. Intrabar Stop Hit Check
        if bar.low <= trade.current_stop:
            exit_px = trade.current_stop
            reason = "BREAKEVEN_STOP" if trade.is_breakeven else "STOP"
            trade.exits.append(
                ExitRecord(reason=reason, price=exit_px, qty=trade.remaining_qty, time=bar.start)
            )
            trade.remaining_qty = 0
            trade.exit_reason = reason
            trade.is_closed = True
            self._finalize_trade(trade)
            session_completed_trades.append(trade)
            return

        # 4. Intrabar Target Hits Check (Must trade through target: bar.high > target)
        while trade.target_idx < len(trade.targets):
            tgt_px, tgt_frac = trade.targets[trade.target_idx]
            if bar.high > tgt_px:
                is_last = (trade.target_idx == len(trade.targets) - 1)
                t_qty = trade.remaining_qty if is_last else int(trade.qty * tgt_frac)
                reason = f"TARGET_{trade.target_idx + 1}"
                trade.exits.append(
                    ExitRecord(reason=reason, price=tgt_px, qty=t_qty, time=bar.start)
                )
                trade.remaining_qty -= t_qty
                trade.target_idx += 1
                trade.exit_reason = reason

                # Auto-trail stop to breakeven (entry reference price)
                trade.current_stop = trade.intent.entry_ref
                trade.is_breakeven = True

                if trade.remaining_qty <= 0:
                    trade.is_closed = True
                    self._finalize_trade(trade)
                    session_completed_trades.append(trade)
                    return
            else:
                break

        # 5. Time Stop Check
        if trade.intent.max_bars is not None and trade.bars_held >= trade.intent.max_bars:
            exit_px = round_to_tick(bar.close - tick_size(bar.close))
            trade.exits.append(
                ExitRecord(reason="TIME_STOP", price=exit_px, qty=trade.remaining_qty, time=bar.end)
            )
            trade.remaining_qty = 0
            trade.exit_reason = "TIME_STOP"
            trade.is_closed = True
            self._finalize_trade(trade)
            session_completed_trades.append(trade)
            return

    def _close_rms_squareoff(self, trade: Trade, last_bar: Bar) -> None:
        exit_px = round_to_tick(last_bar.close - tick_size(last_bar.close))
        trade.exits.append(
            ExitRecord(reason="RMS_SQUAREOFF", price=exit_px, qty=trade.remaining_qty, time=last_bar.end)
        )
        trade.remaining_qty = 0
        trade.exit_reason = "RMS_SQUAREOFF"
        trade.rms_fee = 23.60
        trade.evidence_method = "BAR_MODEL_RMS"
        trade.is_closed = True
        self._finalize_trade(trade)

    def _finalize_trade(self, trade: Trade) -> None:
        buy_charges = DhanFeeEngine.calculate_order(
            OrderSide.BUY, [(trade.entry_price, trade.qty)], ProductType.MIS
        ).total_charges
        sell_charges = sum(
            DhanFeeEngine.calculate_order(
                OrderSide.SELL, [(e.price, e.qty)], ProductType.MIS
            ).total_charges
            for e in trade.exits
        )
        trade.charges = round(buy_charges + sell_charges, 2)
        total_proceeds = sum(e.price * e.qty for e in trade.exits)
        cost_basis = trade.entry_price * trade.qty
        trade.gross_pnl = round(total_proceeds - cost_basis, 2)
        trade.net_pnl = round(trade.gross_pnl - trade.charges - trade.rms_fee, 2)

        risk_budget = trade.qty * (trade.intent.entry_ref - trade.intent.stop_loss)
        trade.net_r = round(trade.net_pnl / risk_budget, 4) if risk_budget > 0 else 0.0

    def _simulate_counterfactual(
        self, intent: SignalIntent, bars: List[Bar], current_bar: Bar
    ) -> None:
        try:
            decision_idx = bars.index(current_bar)
        except ValueError:
            intent.counterfactual_net_r = 0.0
            return

        if decision_idx + 1 >= len(bars):
            intent.counterfactual_net_r = 0.0
            return

        entry_bar = bars[decision_idx + 1]
        entry_px = round_to_tick(entry_bar.open + tick_size(entry_bar.open))
        risk_per_share = abs(intent.entry_ref - intent.stop_loss)
        if risk_per_share <= 0:
            intent.counterfactual_net_r = 0.0
            return

        qty = min(
            int(self.config.risk_budget_rs // risk_per_share),
            int(self.config.slot_cap_rs // intent.entry_ref),
        )
        if qty <= 0:
            qty = 100

        trade = Trade(
            symbol=intent.symbol,
            strategy=intent.strategy,
            entry_time=entry_bar.start,
            entry_price=entry_px,
            qty=qty,
            stop_loss=intent.stop_loss,
            targets=intent.targets,
            intent=intent,
            session=entry_bar.session_date,
        )

        completed: List[Trade] = []
        for b in bars[decision_idx + 1 :]:
            self._process_bar_for_trade(trade, b, completed)
            if trade.is_closed:
                break

        if not trade.is_closed:
            self._close_rms_squareoff(trade, bars[-1])

        intent.counterfactual_net_r = trade.net_r
