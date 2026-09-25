"""
research/backtest/study.py
==========================
End-to-end backtesting study runner and research pipeline.
Orchestrates universe filtering, event-driven engine execution, Track 2 gate evaluation,
and exports canonical summary.json, signals.csv, trades.csv, and tear_sheet.html.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from research.backtest.bars import CandleStore
from research.backtest.engine import BacktestEngine, EngineConfig, EngineResult
from research.backtest.metrics import evaluate_gate, r_multiple_summary
from research.backtest.strategies import OrbAdapter, StrategyAdapter, default_adapters
from research.backtest.tear_sheet import render_tear_sheet
from research.backtest.universe import PointInTimeUniverse


def run_study(
    store: CandleStore,
    universe: PointInTimeUniverse,
    out_dir: Path | str,
    adapters: Optional[Sequence[StrategyAdapter]] = None,
    sectors: Optional[Mapping[str, str]] = None,
    config: Optional[EngineConfig] = None,
    min_baseline_sessions: int = 20,
) -> Dict[str, Any]:
    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    if adapters is None:
        adapters = default_adapters()

    for a in adapters:
        if isinstance(a, OrbAdapter):
            a.min_baseline_bars = min_baseline_sessions

    cfg = config or EngineConfig()
    engine = BacktestEngine(
        store=store,
        universe=universe,
        adapters=adapters,
        config=cfg,
        sectors=sectors,
    )
    res: EngineResult = engine.run()

    # Gate Evaluation
    sessions_observed = len(res.dates)
    trade_dicts = [
        {
            "symbol": t.symbol,
            "session": t.session,
            "net_r": t.net_r,
            "evidence_class": t.evidence_class,
        }
        for t in res.trades
    ]
    gate = evaluate_gate(trades=trade_dicts, sessions_observed=sessions_observed)

    # Strategy Summaries
    strat_summaries: Dict[str, Dict[str, Any]] = {}
    for a in adapters:
        s_trades = [t for t in res.trades if t.strategy == a.name]
        r_list = [t.net_r for t in s_trades]
        sess_list = [t.session for t in s_trades]
        strat_summaries[a.name] = {
            "summary": r_multiple_summary(r_list, sess_list),
            "net_r": r_list,
            "daily_pnl": {d: sum(t.net_pnl for t in s_trades if t.session == d) for d in res.dates},
        }

    # 1. summary.json
    summary_payload = {
        "gate": gate,
        "sessions_observed": sessions_observed,
        "total_trades": len(res.trades),
        "total_signals": len(res.signals),
        "decision_counts": res.decision_counts,
        "pnl_by_date": {d.isoformat(): pnl for d, pnl in res.daily_pnl.items()},
    }
    (out_path / "summary.json").write_text(
        json.dumps(summary_payload, indent=2, default=str), encoding="utf-8"
    )

    # 2. signals.csv
    with open(out_path / "signals.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "strategy", "symbol", "side", "signal_time", "entry_ref",
            "stop_loss", "disposition", "counterfactual_net_r"
        ])
        for s in res.signals:
            writer.writerow([
                s.strategy, s.symbol, s.side,
                s.signal_time.isoformat() if s.signal_time else "",
                s.entry_ref, s.stop_loss, s.disposition,
                s.counterfactual_net_r if s.counterfactual_net_r is not None else ""
            ])

    # 3. trades.csv
    with open(out_path / "trades.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "symbol", "strategy", "entry_time", "entry_price", "qty",
            "stop_loss", "exit_reason", "gross_pnl", "charges", "rms_fee",
            "net_pnl", "net_r", "evidence_method"
        ])
        for t in res.trades:
            writer.writerow([
                t.symbol, t.strategy, t.entry_time.isoformat(), t.entry_price,
                t.qty, t.stop_loss, t.exit_reason, t.gross_pnl, t.charges,
                t.rms_fee, t.net_pnl, t.net_r, t.evidence_method
            ])

    # 4. tear_sheet.html
    render_tear_sheet(
        out_path=out_path / "tear_sheet.html",
        title="Track 2 Backtest Study",
        portfolio={"dates": res.dates, "daily_returns": res.daily_returns},
        strategies=strat_summaries,
        notes=["Track 2 Automated Study Pipeline", "Fails closed under Rule 1 Gate for bar-modelled fills"],
        gate=gate,
    )

    return {
        "gate": gate,
        "summary": summary_payload,
        "trades": res.trades,
        "signals": res.signals,
    }
