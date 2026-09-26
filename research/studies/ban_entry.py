"""
research/studies/ban_entry.py
=============================
BAN_ENTRY_SHORT, design-set evaluation (DRAFT idea from the 26 Sep exploratory scan, T0062-T0075).

Idea: on the FIRST session of an F&O-ban spell (MWPL > 95%: no new F&O positions; the list for day D is
published by NSE the evening before), the stock falls from the open (design scan: -0.88% market-adjusted
open->close, t -6.5, every year negative). Trade: intraday MIS SHORT at the open, covered at the 15:05 policy exit.

Model (the shared research simulator, signal_sim.simulate_signal, with the engine's own fill rules):
- the decision is made BEFORE the session (the ban list is known the evening before; the pre-open equilibrium
  price is published at 09:08). A synthetic 09:00-09:15 bar priced at the 09:15 open is placed before the
  day's bars, so the simulator enters at the 09:15 open with the normal slippage (1 tick) and clamp;
- stop: SL-limit at entry x (1 + s) for s in {2%, 3%} (two pre-declared variants, both registered);
  r_basis stop_limit; no targets; exit at the 15:05 policy exit;
- size: Adjusted A1 (Rs 1,500 risk, Rs 38,000 at the worst admissible entry); MIS fees (DhanFeeEngine).
Eligibility: the stock was ELIGIBLE in the universe table on the previous session (F&O member, price >= Rs 10,
DTV20 >= Rs 30 Cr) and is not in the ban on the previous session (first day of the spell).

NOT modelled, to be confirmed before any live use: whether the broker permits MIS shorts in F&O-ban stocks, and
whether an auction-open fill is achievable (pre-open order entry 09:00-09:08).
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))


def ban_entries(snap: Path, start: str, end: str) -> pd.DataFrame:
    """(symbol, day) of the first session of every ban spell whose previous session was eligible."""
    b = pd.read_parquet(snap / "events" / "fo_ban.parquet")
    b["day"] = b.trade_date.astype(str)
    b = b[["symbol", "day"]].drop_duplicates()
    u = pd.read_parquet(snap / "reference" / "universe_daily.parquet", columns=["symbol", "session", "eligible"])
    days = sorted(u.session.astype(str).unique())
    idx = {d: i for i, d in enumerate(days)}
    b["i"] = b.day.map(idx)
    b = b.dropna(subset=["i"]).sort_values(["symbol", "i"])
    first = b[b.groupby("symbol")["i"].diff() != 1].copy()
    first["prev"] = [days[int(i) - 1] if int(i) > 0 else None for i in first.i]
    elig = set(zip(u[u.eligible].symbol, u[u.eligible].session.astype(str)))
    first = first[[(s, p) in elig for s, p in zip(first.symbol, first.prev)]]
    return first[(first.day >= start) & (first.day <= end)][["symbol", "day"]].reset_index(drop=True)


def simulate(store: Any, events: pd.DataFrame, stop_pct: float, slippage_ticks: int = 1) -> List[Dict[str, Any]]:
    from research.backtest.bars import Bar, round_to_tick, tick_size
    from research.backtest.engine import EngineConfig
    from research.studies.signal_sim import simulate_signal, size_qty

    ecfg = EngineConfig(var_elm_rate=0.20, allow_shorts=True, r_basis="stop_limit", stop_limit_offset_pct=0.005,
                        entry_slippage_ticks=slippage_ticks, stop_slippage_ticks=slippage_ticks,
                        exit_slippage_ticks=slippage_ticks)
    cfg = ecfg.sim_config()
    out = []
    for ev in events.itertuples():
        d = date.fromisoformat(ev.day)
        bars = store.bars(ev.symbol, d)
        if len(bars) < 20:
            out.append({"symbol": ev.symbol, "day": ev.day, "disposition": "NO_BARS"})
            continue
        o = bars[0].open
        pre = Bar(ev.symbol, bars[0].start - timedelta(minutes=15), 15, o, o, o, o, 0)      # pre-open decision bar
        stop = round_to_tick(o * (1 + stop_pct), "up")
        worst = o * (1 + ecfg.clamp_pct)
        cap_px = round_to_tick(worst, "up") + ecfg.entry_slippage_ticks * tick_size(worst)
        qty = size_qty(o, stop, "SELL", ecfg.risk_budget_rs, ecfg.slot_cap_rs, ecfg.stop_limit_offset_pct, notional_px=cap_px)
        sim = simulate_signal([pre] + list(bars), 0, "SELL", o, stop, [], None, qty, cfg, d)
        out.append({"symbol": ev.symbol, "day": ev.day, "disposition": sim.disposition, "qty": qty,
                    "entry": sim.entry_price, "exit_reason": sim.exit_reason, "net_r": sim.net_r, "gross_r": sim.gross_r,
                    "fee_r": sim.fee_r, "slip_r": sim.slip_r, "net_pnl_rs": sim.net_pnl_rs, "risk_rs": sim.risk_rs})
    return out


def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    from research.decision.stats import clustered_se

    df = pd.DataFrame(rows)
    f = df[(df.disposition == "FILLED") & np.isfinite(df.net_r.astype(float))] if "net_r" in df else df.iloc[:0]
    net = f.net_r.astype(float).to_numpy()
    se = clustered_se(list(net), list(f.day)) if len(net) > 1 else math.nan
    by_year = {}
    for y, g in f.groupby(f.day.str[:4]):
        v = g.net_r.astype(float).to_numpy()
        s = clustered_se(list(v), list(g.day)) if len(v) > 1 else math.nan
        by_year[y] = {"n": len(v), "mean_net_r": round(float(v.mean()), 4),
                      "t": round(float(v.mean() / s), 2) if math.isfinite(s) and s > 0 else None}
    return {"events": len(df), "filled": len(f), "dispositions": df.disposition.value_counts().to_dict(),
            "mean_net_r": float(net.mean()) if len(net) else None, "se_cluster_by_day": se,
            "t_cluster": float(net.mean() / se) if len(net) > 1 and math.isfinite(se) and se > 0 else None,
            "mean_gross_r": float(f.gross_r.astype(float).mean()) if len(f) else None,
            "mean_fee_r": float(f.fee_r.astype(float).mean()) if len(f) else None,
            "mean_slip_r": float(f.slip_r.astype(float).mean()) if len(f) else None,
            "mean_net_pnl_rs": float(f.net_pnl_rs.astype(float).mean()) if len(f) else None,
            "total_net_pnl_rs": float(f.net_pnl_rs.astype(float).sum()) if len(f) else None,
            "exit_reasons": f.exit_reason.value_counts().to_dict() if len(f) else {}, "by_year": by_year}


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths
    from research.data.store_parquet import ParquetCandleStore

    ap = argparse.ArgumentParser(description="BAN_ENTRY_SHORT design-set evaluation")
    ap.add_argument("--start", default="2022-01-03")
    ap.add_argument("--end", default="2024-09-30")
    ap.add_argument("--stops", default="0.02,0.03")
    args = ap.parse_args(argv)
    if args.end > "2024-09-30":
        print("REFUSED: design window only (the holdout is read once, after a locked pre-registration)")
        return 2
    snap = paths.history_dir()
    store = ParquetCandleStore()
    ev = ban_entries(snap, args.start, args.end)
    res = {"window": [args.start, args.end], "events": len(ev), "variants": {}}
    out_dir = paths.ensure(paths.outputs_dir() / "ban_entry" / datetime.now(IST).strftime("%Y%m%d_%H%M%S"))
    for s in (float(x) for x in args.stops.split(",")):
        rows = simulate(store, ev, s)
        res["variants"][f"stop_{s:.0%}"] = summarise(rows)
        pd.DataFrame(rows).to_parquet(out_dir / f"signals_stop{int(s * 100)}.parquet", index=False)
    (out_dir / "result.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))
    print(f"written: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
