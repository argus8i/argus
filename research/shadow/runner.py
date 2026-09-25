"""
research/shadow/runner.py
=========================
ShadowRunner: bar-close allocation of the day's emitted signals under Adjusted A1 (plan P6.5, P8.2; Yashu's
mandate of 26 Sep 2026). Paper/research only: `allow_live=True` is refused (AGENTS.md rule 1).

Signals come from the strategy adapters through BacktestEngine (a one-day run: every decision is made on a
PointInTimeView that ends at the bar close; research/shadow/run_day.py journals them live). Emission does not
depend on allocation, so the runner takes the emitted signals and, at every decision time t in order:
  1. applies the book events that happened up to t (entry fills at their fill time, exits at their exit time,
     unfilled entries cancelled one bar later), so slots and exposure are those a real book had at t;
  2. sizes each signal exactly as the engine does: qty = size_qty(entry_ref, stop, side, Rs 1,500, Rs 38,000
     at the worst admissible entry price), then times the VIX multiplier m (sizing.vol_target_multiplier:
     stale or missing VIX gives m = 0, so no entry: fail closed);
  3. prices it for EXPLOIT: expected_net_r = shrunk gross estimate - the signal's own fee and slippage R;
  4. calls allocator.allocate (SHADOW: pre-registered priority; EXPLOIT: promoted strategies with positive
     expected_net_r only) with the book's active positions and pending reservations, free cash, weekly
     residual clusters (1 per cluster) and sectors (2 per sector);
  5. reserves every allocated signal in the ExposureBook at qty x worst admissible entry price, simulates its
     fills on the day's bars (signal_sim, the engine's simulation) and schedules the fill/exit events.
Every emitted signal becomes one ledger row: allocated ones carry their E1 outcome at the allocated qty,
dropped ones their drop reason and E1_CF counterfactual. At the end of the session the book must be flat.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from research.backtest.bars import round_to_tick, tick_size
from research.decision.allocator import EXPLOIT, SHADOW, Candidate, ExposureBook, allocate
from research.decision.estimator import StrategyEstimate, expected_net_r, signal_cost
from research.decision.sizing import (AGGREGATE_EXPOSURE_CAP_RS, MAX_SLOTS, RISK_BUDGET_RS, SLOT_CAP_RS,
                                      Multiplier, final_qty, vol_target_multiplier)
from research.studies.signal_sim import side_sign, simulate_signal, size_qty

IST = timezone(timedelta(hours=5, minutes=30))
VIX = "IDX:INDIAVIX"
DEFAULT_PRIORITIES = {"RESID_REV": 1, "RESID_REV_NF": 1, "ORB_PROD": 2}


class LiveTradingRefused(RuntimeError):
    """AGENTS.md rule 1: real capital is prohibited; the runner never trades live."""


@dataclass
class RunnerConfig:
    mode: str = SHADOW
    allow_live: bool = False
    priorities: Mapping[str, int] = field(default_factory=lambda: dict(DEFAULT_PRIORITIES))
    promoted: Optional[Set[str]] = None
    risk_budget_rs: float = RISK_BUDGET_RS
    slot_cap_rs: float = SLOT_CAP_RS
    aggregate_cap_rs: float = AGGREGATE_EXPOSURE_CAP_RS
    max_slots: int = MAX_SLOTS
    clamp_pct: float = 0.0010
    slippage_ticks: int = 1
    stop_limit_offset_pct: float = 0.005
    r_basis: str = "stop_limit"
    seed: int = 0
    use_vix: bool = True


@dataclass
class SessionReport:
    session: str
    signals: int = 0
    allocated: int = 0
    drops: Counter = field(default_factory=Counter)
    peak_exposure_rs: float = 0.0
    peak_slots: int = 0
    net_pnl_rs: float = 0.0
    trades_net_r: List[float] = field(default_factory=list)
    vix_multipliers: Counter = field(default_factory=Counter)
    book_flat: bool = True
    breaches: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {"session": self.session, "signals": self.signals, "allocated": self.allocated,
                "drops": dict(self.drops), "peak_exposure_rs": round(self.peak_exposure_rs, 2),
                "peak_exposure_share": round(self.peak_exposure_rs / AGGREGATE_EXPOSURE_CAP_RS, 4),
                "peak_slots": self.peak_slots, "net_pnl_rs": round(self.net_pnl_rs, 2),
                "trades": len(self.trades_net_r), "trades_net_r": [round(x, 4) for x in self.trades_net_r],
                "vix_multipliers": dict(self.vix_multipliers), "book_flat": self.book_flat,
                "exposure_breaches": self.breaches}


class ShadowRunner:
    def __init__(self, store: Any, sectors: Mapping[str, str], *, config: Optional[RunnerConfig] = None,
                 estimates: Optional[Mapping[str, StrategyEstimate]] = None,
                 clusters: Optional[Callable[[date], Mapping[str, str]]] = None) -> None:
        from research.backtest.engine import EngineConfig

        self.cfg = config or RunnerConfig()
        if self.cfg.allow_live:
            raise LiveTradingRefused("allow_live=True refused: paper/research only (AGENTS.md rule 1)")
        if self.cfg.mode not in (SHADOW, EXPLOIT):
            raise ValueError(f"mode must be SHADOW or EXPLOIT, got {self.cfg.mode!r}")
        self.store, self.sectors = store, dict(sectors)
        self.estimates = dict(estimates or {})
        self.clusters = clusters or (lambda d: {})
        k = self.cfg.slippage_ticks
        self.sim_cfg = EngineConfig(var_elm_rate=0.20, allow_shorts=True, r_basis=self.cfg.r_basis,
                                    stop_limit_offset_pct=self.cfg.stop_limit_offset_pct, clamp_pct=self.cfg.clamp_pct,
                                    entry_slippage_ticks=k, stop_slippage_ticks=k,
                                    exit_slippage_ticks=k).sim_config()

    # ------------------------------------------------------------------ inputs at a decision time
    def vix_multiplier(self, day: date, t: datetime) -> Multiplier:
        if not self.cfg.use_vix:
            return Multiplier(1.0, "VIX_NOT_USED")
        have = VIX in self.store.symbols
        bars = [b for b in self.store.bars(VIX, day) if b.end <= t] if have else []
        hist = [d.close for d in self.store.daily_before(VIX, day)] if have else []
        if not bars:
            return vol_target_multiplier(None, None, hist, t)
        return vol_target_multiplier(bars[-1].close, bars[-1].end, hist, t)

    def worst_entry(self, entry_ref: float) -> float:
        """The price the slot cap and the reservation use; identical to BacktestEngine (Adjusted A1)."""
        worst = entry_ref * (1 + self.cfg.clamp_pct)
        return round_to_tick(worst, "up") + self.cfg.slippage_ticks * tick_size(worst)

    def plan(self, s: Any, day: date, mult: Multiplier) -> Tuple[Candidate, int, float]:
        """(candidate, qty, reserve_px) for one emitted signal."""
        reserve_px = self.worst_entry(s.entry_ref)
        qty0 = size_qty(s.entry_ref, s.stop_loss, s.side, self.cfg.risk_budget_rs, self.cfg.slot_cap_rs,
                        self.cfg.stop_limit_offset_pct, notional_px=reserve_px)
        qty = final_qty(qty0, mult)
        sg = side_sign(s.side)
        risk_px = s.stop_loss * (1 - sg * self.cfg.stop_limit_offset_pct) if self.cfg.r_basis == "stop_limit" \
            else s.stop_loss
        est = self.estimates.get(s.strategy) or StrategyEstimate.empty(s.strategy, self.cfg.r_basis)
        e = expected_net_r(est, signal_cost(s.entry_ref, risk_px, s.side, qty)) if qty > 0 else math.nan
        cand = Candidate(strategy_id=s.strategy, symbol=s.symbol, side=s.side, session=day,
                         priority=int(self.cfg.priorities.get(s.strategy, 9)),
                         priority_score=float(getattr(s, "priority_score", 0.0) or 0.0), expected_net_r=e,
                         se=est.se_cluster, risk_rs=abs(s.entry_ref - risk_px) * qty, m=mult.m,
                         notional=qty * reserve_px, cluster=self.clusters(day).get(s.symbol),
                         sector=self.sectors.get(s.symbol))
        return cand, qty, reserve_px

    # ------------------------------------------------------------------ one session
    def process_session(self, day: date, signals: Sequence[Any]) -> Tuple[SessionReport, List[Dict[str, Any]]]:
        rep = SessionReport(day.isoformat(), signals=len(signals))
        book = ExposureBook(self.cfg.max_slots, self.cfg.slot_cap_rs, self.cfg.aggregate_cap_rs)
        events: List[Tuple[datetime, int, str, str, int, float]] = []    # (time, seq, kind, oid, qty, px)
        rows: List[Dict[str, Any]] = []
        by_time: Dict[datetime, List[Any]] = defaultdict(list)
        for s in signals:
            by_time[s.signal_time].append(s)
        seq = 0

        def apply_until(t: Optional[datetime]) -> None:
            events.sort(key=lambda e: (e[0], e[1]))
            while events and (t is None or events[0][0] <= t):
                _, _, kind, oid, qty, px = events.pop(0)
                if kind == "FILL":
                    book.fill(oid, qty, px)
                elif kind == "CANCEL":
                    book.cancel(oid)
                else:
                    book.close(oid, qty)
                rep.peak_exposure_rs = max(rep.peak_exposure_rs, book.exposure())

        for t in sorted(by_time):
            apply_until(t)
            mult = self.vix_multiplier(day, t)
            rep.vix_multipliers[re.sub(r"VIX_HISTORY_\d+_OF_\d+", "VIX_HISTORY_SHORT", mult.reason)] += 1
            planned: Dict[Tuple[str, str], Tuple[Any, int, float]] = {}
            cands: List[Candidate] = []
            for s in sorted(by_time[t], key=lambda x: (x.strategy, x.symbol)):
                cand, qty, px = self.plan(s, day, mult)
                if qty <= 0:
                    why = "SIZE_ZERO:" + (mult.reason if mult.m <= 0 else "GEOMETRY")
                    rows.append(self._row(s, day, "DROPPED:" + why, None, 0, mult))
                    rep.drops[re.sub(r"VIX_HISTORY_\d+_OF_\d+", "VIX_HISTORY_SHORT", why)] += 1
                    continue
                planned[(s.strategy, s.symbol)] = (s, qty, px)
                cands.append(cand)
            act, pen = book.positions()
            free_cash = max(0.0, self.cfg.aggregate_cap_rs - book.exposure())
            alloc = allocate(cands, mode=self.cfg.mode, open_positions=act, pending_orders=pen, free_cash=free_cash,
                             promoted=self.cfg.promoted, seed=self.cfg.seed, max_slots=self.cfg.max_slots,
                             slot_cap_rs=self.cfg.slot_cap_rs, aggregate_cap_rs=self.cfg.aggregate_cap_rs)
            for c, why in alloc.dropped:
                s, qty, _ = planned[(c.strategy_id, c.symbol)]
                rows.append(self._row(s, day, f"DROPPED:{why}", None, qty, mult))
                rep.drops[why] += 1
            for c in alloc.allocated:
                s, qty, px = planned[(c.strategy_id, c.symbol)]
                oid = f"{day.isoformat()}|{s.strategy}|{s.symbol}|{t.isoformat()}"
                book.reserve(oid, s.symbol, s.side, qty, px, c.cluster, c.sector)
                rep.allocated += 1
                rep.peak_exposure_rs = max(rep.peak_exposure_rs, book.exposure())
                rep.peak_slots = max(rep.peak_slots, book.slots_used())
                bars = self.store.bars(s.symbol, day)
                i = next((k for k, b in enumerate(bars) if b.end == t), None)
                sim = simulate_signal(bars, i, s.side, s.entry_ref, s.stop_loss, s.targets, s.max_bars, qty,
                                      self.sim_cfg, day) if i is not None else None
                if sim is not None and sim.filled:
                    seq += 1
                    events.append((sim.entry_time, seq, "FILL", oid, qty, sim.entry_price))
                    for ex in sim.exits:
                        seq += 1
                        events.append((ex.time, seq, "CLOSE", oid, int(ex.qty), ex.price))
                    rep.net_pnl_rs += sim.net_pnl_rs
                    if math.isfinite(sim.net_r):
                        rep.trades_net_r.append(sim.net_r)
                else:
                    seq += 1
                    events.append((t + timedelta(minutes=15), seq, "CANCEL", oid, 0, 0.0))
                rows.append(self._row(s, day, "ALLOCATED", sim, qty, mult))
        apply_until(None)
        rep.book_flat = book.slots_used() == 0 and book.exposure() < 1e-6
        rep.breaches = len(book.breaches)
        return rep, rows

    # ------------------------------------------------------------------ ledger rows
    def _row(self, s: Any, day: date, disposition: str, sim: Any, qty: int, mult: Multiplier) -> Dict[str, Any]:
        tg = list(s.targets)
        row: Dict[str, Any] = {
            "strategy_id": s.strategy, "session": day, "decision_ts": s.signal_time, "symbol": s.symbol,
            "side": s.side, "decision_price": s.entry_ref, "entry_ref": s.entry_ref, "stop_trigger": s.stop_loss,
            "t1": tg[0][0] if tg else None, "t2": tg[1][0] if len(tg) > 1 else None, "h_eff": s.max_bars,
            "qty_planned": qty, "notional_planned": qty * self.worst_entry(s.entry_ref) if qty else None,
            "r_basis": self.cfg.r_basis, "vix_multiplier": mult.m, "disposition": disposition,
            "allocated": disposition == "ALLOCATED", "features": s.diagnostics or None}
        if sim is not None and disposition == "ALLOCATED":
            row.update({"stop_limit": sim.stop_limit, "evidence_class": "E1",
                        "entry_fill_price": sim.entry_price if sim.filled else None,
                        "entry_fill_ts": sim.entry_time, "exit_reason": sim.exit_reason or sim.disposition,
                        "exit_fills": [{"price": e.price, "qty": e.qty, "reason": e.reason, "time": e.time.isoformat()}
                                       for e in sim.exits],
                        "gross_pnl_rs": sim.gross_pnl_rs, "fees_rs": sim.fees_rs, "slippage_rs": sim.slippage_rs,
                        "rms_fee_rs": sim.rms_fee_rs, "net_pnl_rs": sim.net_pnl_rs, "gross_r": sim.gross_r,
                        "fee_r": sim.fee_r, "slip_r": sim.slip_r, "net_r": sim.net_r,
                        "risk_rs_planned": sim.risk_rs})
        else:
            row.update({"evidence_class": "E1_CF", "exit_reason": s.counterfactual_exit_reason or None,
                        "gross_r": s.counterfactual_gross_r, "fee_r": s.counterfactual_fee_r,
                        "slip_r": s.counterfactual_slip_r, "net_r": s.counterfactual_net_r})
        return row


def ledger_rows(rows: Sequence[Mapping[str, Any]], *, run_id: str, mode: str, strategy_version: Mapping[str, str],
                **prov: Any) -> List[Dict[str, Any]]:
    """Runner rows -> Ledger.append rows (identity and provenance filled in)."""
    return [{**r, "run_id": run_id, "mode": mode,
             "strategy_version": strategy_version.get(r["strategy_id"], "unversioned"), **prov} for r in rows]
