"""
research/studies/portfolio.py
=============================
P7.5 portfolio simulation (Yashu's mandate of 26 Sep 2026): the emitted signals of a study run, allocated bar
by bar by research/shadow/runner.ShadowRunner under Adjusted A1 (3 slots, Rs 38,000 per slot at the worst
admissible entry, Rs 1,14,000 aggregate over active positions plus pending reservations), one position per
weekly residual cluster, two per sector, and the VIX multiplier (m = 0 when VIX is stale or missing).

This answers a different question from the per-signal statistics: what a 3-slot book would have earned in
rupees, after the slots, clusters, sectors and VIX sizing turn most signals away. Clusters are computed over
the symbols that emitted a signal in the run (weekly, from data strictly before each week).
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from typing import Any, Dict, Mapping, Optional, Sequence

import numpy as np


def runner_portfolio(store: Any, signals: Sequence[Any], sectors: Mapping[str, str],
                     config: Optional[Any] = None) -> Dict[str, Any]:
    from research.decision.clusters import weekly_clusters
    from research.shadow.runner import RunnerConfig, ShadowRunner

    sig = [s for s in signals if s.signal_time is not None]
    by_day: Dict[date, list] = defaultdict(list)
    for s in sig:
        by_day[s.signal_time.date()].append(s)
    days = sorted(by_day)
    if not days:
        return {"sessions_with_signals": 0}
    syms = sorted({s.symbol for s in sig})
    cl = weekly_clusters(store, syms, days)
    runner = ShadowRunner(store, sectors, config=config or RunnerConfig(), clusters=lambda d: cl.get(d, {}))
    daily, net_r, drops, vix = [], [], Counter(), Counter()
    util, allocated, flat, breaches = [], 0, 0, 0
    for d in days:
        rep, _ = runner.process_session(d, by_day[d])
        daily.append(rep.net_pnl_rs)
        net_r += rep.trades_net_r
        drops.update(rep.drops)
        vix.update(rep.vix_multipliers)
        util.append(rep.peak_exposure_rs)
        allocated += rep.allocated
        flat += int(rep.book_flat)
        breaches += rep.breaches
    d_arr = np.array(daily)
    eq = np.cumsum(d_arr)
    r = np.array(net_r)
    return {"sessions_with_signals": len(days), "signals": len(sig), "allocated": allocated,
            "trades_filled": int(r.size), "net_pnl_rs": float(d_arr.sum()),
            "mean_trade_net_r": float(r.mean()) if r.size else None,
            "win_rate": float(np.mean(r > 0)) if r.size else None,
            "daily_pnl_sd_rs": float(d_arr.std(ddof=1)) if d_arr.size > 1 else None,
            "max_drawdown_rs": float(np.max(np.maximum.accumulate(eq) - eq)) if eq.size else 0.0,
            "mean_peak_exposure_rs": float(np.mean(util)), "max_peak_exposure_rs": float(np.max(util)),
            "drop_reasons": dict(drops), "vix_multiplier_reasons": dict(vix),
            "sessions_flat_at_close": f"{flat}/{len(days)}", "exposure_breaches": breaches,
            "clusters": "weekly residual clusters over the signalling symbols (research/decision/clusters.py)"}
