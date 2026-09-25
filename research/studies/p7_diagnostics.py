"""
research/studies/p7_diagnostics.py
==================================
Descriptive breakdowns for the P7 report (Yashu's mandate of 26 Sep 2026). No choices are made from them.

    python -m research.studies.p7_diagnostics --orb <orbprod run dir> --out research/outputs/p7/diagnostics.json

1. ORB_PROD by (a) stop distance |entry_ref - stop| / entry_ref (<0.5%, 0.5-1.0%, >1.0%), (b) RVOL tercile
   (the production volume_ratio at the signal), (c) VIX tercile (INDIA VIX previous daily close: known before
   the session), (d) entry slot (signal bar start 09:30, 09:45, 10:00 and later). Each cell: n, mean gross /
   fee / slippage / net R, and the day-clustered t of net R.
2. Cost versus edge for a 15-minute MIS trade: DhanFeeEngine round trip on a Rs 38,000 position (the A1 slot)
   at several prices, slippage of 1 tick per side, the break-even favourable move, and the cost in R at
   stop distances of 0.5%, 1% and 2% (risk = notional x stop distance when the slot cap binds).
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np


def _cell(df: Any) -> Dict[str, Any]:
    from research.decision.stats import clustered_se

    n = len(df)
    if n == 0:
        return {"n": 0}
    net = df["net_r"].to_numpy(dtype=float)
    se = clustered_se(list(net), list(df["day"])) if n > 1 else math.nan
    return {"n": n, "days": int(df["day"].nunique()), "gross_r": round(float(df["gross_r"].mean()), 4),
            "fee_r": round(float(df["fee_r"].mean()), 4), "slip_r": round(float(df["slip_r"].mean()), 4),
            "net_r": round(float(net.mean()), 4),
            "t_net": round(float(net.mean() / se), 2) if math.isfinite(se) and se > 0 else None}


def orb_breakdown(run_dir: Path, vix_daily: Dict[date, float]) -> Dict[str, Any]:
    import pandas as pd

    df = pd.read_parquet(Path(run_dir) / "signals.parquet")
    t = pd.to_datetime(df["signal_time"], utc=True).dt.tz_convert("Asia/Kolkata")
    df["day"] = t.dt.date
    df["stop_pct"] = (df["entry_ref"] - df["stop_loss"]).abs() / df["entry_ref"] * 100
    df["rvol"] = [json.loads(d).get("volume_ratio") for d in df["diagnostics"]]
    df["slot"] = (t - pd.Timedelta(minutes=15)).dt.strftime("%H:%M")      # signal_time is the bar close
    days = sorted(vix_daily)

    def prev_vix(d: date) -> Optional[float]:
        k = bisect.bisect_left(days, d)
        return vix_daily[days[k - 1]] if k > 0 else None

    df["vix_prev"] = [prev_vix(d) for d in df["day"]]
    out: Dict[str, Any] = {"all": _cell(df)}
    bins = {"<0.5%": df["stop_pct"] < 0.5, "0.5-1.0%": (df["stop_pct"] >= 0.5) & (df["stop_pct"] <= 1.0),
            ">1.0%": df["stop_pct"] > 1.0}
    out["stop_distance"] = {k: _cell(df[m]) for k, m in bins.items()}
    r = df.dropna(subset=["rvol"])
    q = np.quantile(r["rvol"].astype(float), [1 / 3, 2 / 3])
    out["rvol_tercile"] = {f"T1 (<= {q[0]:.2f})": _cell(r[r["rvol"] <= q[0]]),
                           f"T2 ({q[0]:.2f}-{q[1]:.2f})": _cell(r[(r["rvol"] > q[0]) & (r["rvol"] <= q[1])]),
                           f"T3 (> {q[1]:.2f})": _cell(r[r["rvol"] > q[1]])}
    v = df.dropna(subset=["vix_prev"])
    qv = np.quantile(v["vix_prev"].astype(float), [1 / 3, 2 / 3])
    out["vix_tercile"] = {f"T1 (<= {qv[0]:.2f})": _cell(v[v["vix_prev"] <= qv[0]]),
                          f"T2 ({qv[0]:.2f}-{qv[1]:.2f})": _cell(v[(v["vix_prev"] > qv[0]) & (v["vix_prev"] <= qv[1])]),
                          f"T3 (> {qv[1]:.2f})": _cell(v[v["vix_prev"] > qv[1]])}
    out["entry_slot"] = {"09:30": _cell(df[df["slot"] == "09:30"]), "09:45": _cell(df[df["slot"] == "09:45"]),
                         "10:00+": _cell(df[df["slot"] >= "10:00"])}
    out["stop_pct_quantiles"] = {str(p): round(float(np.quantile(df["stop_pct"], p)), 3)
                                 for p in (0.1, 0.25, 0.5, 0.75, 0.9)}
    return out


def cost_vs_edge(notional: float = 38000.0) -> Dict[str, Any]:
    from research.backtest.bars import tick_size
    from research.backtest.cost_model import DhanFeeEngine

    rows = []
    for price in (100.0, 250.0, 500.0, 1000.0, 2500.0, 5000.0):
        qty = int(notional // price)
        fees = DhanFeeEngine.calculate_round_trip(price, price, qty).total_charges
        fee_pct = fees / (qty * price) * 100
        slip_pct = 2 * tick_size(price) / price * 100              # 1 tick per side
        be = fee_pct + slip_pct
        rows.append({"price": price, "qty": qty, "fees_rs": round(fees, 2), "fee_pct": round(fee_pct, 4),
                     "slip_1tick_per_side_pct": round(slip_pct, 4), "breakeven_move_pct": round(be, 4),
                     "cost_r_at_stop": {f"{s}%": round(be / s, 3) for s in (0.5, 1.0, 2.0)}})
    return {"notional_rs": notional, "rows": rows,
            "reading": "cost_r_at_stop = break-even move / stop distance: the gross edge in R a strategy needs just "
                       "to pay fees and 1 tick per side when the slot cap binds (risk = notional x stop %)."}


def main(argv: Optional[List[str]] = None) -> int:
    from research.data.store_parquet import ParquetCandleStore

    ap = argparse.ArgumentParser(description="P7 descriptive diagnostics")
    ap.add_argument("--orb", required=True, help="an orbprod run directory with the diagnostics table")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    store = ParquetCandleStore()
    vix = {d.day: d.close for d in store.daily("IDX:INDIAVIX")}
    res = {"orb_prod": orb_breakdown(Path(args.orb), vix), "cost_vs_edge": cost_vs_edge(),
           "source_run": str(args.orb)}
    Path(args.out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps({"written": args.out}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
