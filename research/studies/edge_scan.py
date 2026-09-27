"""
research/studies/edge_scan.py
=============================
Exploratory feasibility scan for new Track 2 edges (26 Sep 2026, after P7 rejected/killed all seven strategies).
DESIGN WINDOW ONLY (2022-01-03 .. 2024-09-30), sealed design snapshot, eligible stock-days only. Descriptive:
no rules are fitted, no parameters searched beyond the fixed buckets below. Every block is one registered
exploratory look (trials registry, evidence class E0_EXPLORATORY). A block that looks promising becomes a written
pre-registration tested once on data its rule has never touched, then prospective shadow evidence.

All returns are MARKET-ADJUSTED (stock minus NIFTY 50 over the same interval), in percent, and signed in the
natural direction of the idea. SE is clustered by date (CR1). The question for each block: is the mean move
several times the cost hurdle?  MIS round trip ~0.12% (0.106% fees + 1 tick/side); CNC round trip ~0.28%.

A  results day: 'Financial Result Updates' filed after the previous close (after 15:30) or before 09:15 ->
   day-0 gap, open->close continuation in the gap direction (MIS), and close d0 -> close d+k drift in the day-0
   direction (CNC; k = 1, 3, 5).
B  F&O ban: first day of a ban spell (known the evening before) and first day after it -> open->close and
   open d -> close d+k.
D  overnight gaps |gap| >= 2% and >= 3% -> open->close continuation in the gap direction (MIS).
E  extreme days |market-adjusted close-to-close| >= 5% -> close d -> close d+k reversal (k = 1, 3, 5); the
   long leg (buy after a -5% day) is reported separately because cash shorts cannot be held overnight.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

IST = timezone(timedelta(hours=5, minutes=30))
DESIGN = ("2022-01-03", "2024-09-30")
MIS_HURDLE, CNC_HURDLE = 0.12, 0.28


def _stat(x: pd.Series, dates: pd.Series, hurdle: float) -> Dict[str, Any]:
    from research.decision.stats import clustered_se

    v = np.asarray(x, dtype=float)
    ok = np.isfinite(v)
    v, d = v[ok], np.asarray(dates)[ok]
    if v.size < 2:
        return {"n": int(v.size)}
    se = clustered_se(list(v), list(d))
    m = float(v.mean())
    return {"n": int(v.size), "dates": int(len(set(d))), "mean_pct": round(m, 4), "median_pct": round(float(np.median(v)), 4),
            "se": round(se, 4) if math.isfinite(se) else None, "t": round(m / se, 2) if math.isfinite(se) and se > 0 else None,
            "hurdle_pct": hurdle, "mean_minus_hurdle": round(m - hurdle, 4)}


def load_panel(snap: Path) -> pd.DataFrame:
    rows = [pd.read_parquet(f, columns=["symbol", "day", "open", "high", "low", "close"])
            for f in sorted((snap / "daily").glob("*.parquet"))]
    p = pd.concat(rows)
    p["day"] = p["day"].astype(str)
    p = p.sort_values(["symbol", "day"]).reset_index(drop=True)
    g = p.groupby("symbol")
    p["prev_close"] = g["close"].shift(1)
    for k in (1, 3, 5):
        p[f"close_f{k}"] = g["close"].shift(-k)
    n = p[p.symbol == "IDX:NIFTY50"].set_index("day")[["open", "close", "prev_close", "close_f1", "close_f3", "close_f5"]]
    n.columns = ["n_" + c for c in n.columns]
    p = p[~p.symbol.str.startswith("IDX:")].join(n, on="day")
    pct = lambda a, b: (a / b - 1) * 100
    p["gap"] = pct(p.open, p.prev_close) - pct(p.n_open, p.n_prev_close)
    p["oc"] = pct(p.close, p.open) - pct(p.n_close, p.n_open)
    p["cc"] = pct(p.close, p.prev_close) - pct(p.n_close, p.n_prev_close)
    for k in (1, 3, 5):
        p[f"fwd{k}"] = pct(p[f"close_f{k}"], p.close) - pct(p[f"n_close_f{k}"], p.n_close)
        p[f"ofwd{k}"] = pct(p[f"close_f{k}"], p.open) - pct(p[f"n_close_f{k}"], p.n_open)
    return p


def scan(snap: Path) -> Dict[str, Any]:
    pall = load_panel(snap)
    pall = pall[(pall.day >= DESIGN[0]) & (pall.day <= DESIGN[1])].copy()
    u = pd.read_parquet(snap / "reference" / "universe_daily.parquet", columns=["symbol", "session", "eligible"])
    u = u[u.eligible][["symbol", "session"]].rename(columns={"session": "day"})
    p = pall.merge(u, on=["symbol", "day"])
    out: Dict[str, Any] = {"window": DESIGN, "eligible_stock_days": len(p), "hurdles": {"MIS": MIS_HURDLE, "CNC": CNC_HURDLE}}
    days = sorted(pall.day.unique())

    # A: results announced outside market hours
    a = pd.read_parquet(snap / "events" / "announcements.parquet", columns=["symbol", "disseminated_at", "desc"])
    a = a[a.desc == "Financial Result Updates"].copy()
    t = pd.to_datetime(a.disseminated_at, utc=True).dt.tz_convert("Asia/Kolkata")

    def event_day(ts: pd.Timestamp) -> Optional[str]:
        d = ts.date().isoformat()
        hm = ts.hour * 60 + ts.minute
        if hm >= 15 * 60 + 30:
            k = bisect.bisect_right(days, d)
        elif hm < 9 * 60 + 15:
            k = bisect.bisect_left(days, d)
        else:
            return None
        return days[k] if k < len(days) else None

    a["day"] = [event_day(x) for x in t]
    ev = a.dropna(subset=["day"]).drop_duplicates(["symbol", "day"])[["symbol", "day"]]
    r = p.merge(ev, on=["symbol", "day"])
    sg, s0 = np.sign(r.gap), np.sign(r.cc)
    out["A_results"] = {
        "events": len(r), "abs_gap_median_pct": round(float(r.gap.abs().median()), 3),
        "abs_day0_move_median_pct": round(float(r.cc.abs().median()), 3),
        "day0_open_close_continuation_MIS": _stat(sg * r.oc, r.day, MIS_HURDLE),
        **{f"drift_d{k}_in_day0_direction_CNC": _stat(s0 * r[f"fwd{k}"], r.day, CNC_HURDLE) for k in (1, 3, 5)},
        "drift_d5_long_after_positive_day0_CNC": _stat(r[r.cc > 0].fwd5, r[r.cc > 0].day, CNC_HURDLE)}

    # B: F&O ban spells (banned days are ineligible, so the unfiltered panel is used)
    idx = {d: i for i, d in enumerate(days)}
    b = pd.read_parquet(snap / "events" / "fo_ban.parquet")
    b["day"] = b.trade_date.astype(str)
    b = b[["symbol", "day"]].drop_duplicates()
    b["i"] = b.day.map(idx)
    b = b.dropna(subset=["i"]).sort_values(["symbol", "i"])
    b["new"] = b.groupby("symbol")["i"].diff() != 1
    entry = b[b.new][["symbol", "day"]]
    last = b[b.groupby("symbol")["i"].shift(-1) != b["i"] + 1]
    exit_days = pd.DataFrame({"symbol": last.symbol.values,
                              "day": [days[int(i) + 1] if int(i) + 1 < len(days) else None for i in last.i]}).dropna()
    re, rx = pall.merge(entry, on=["symbol", "day"]), pall.merge(exit_days, on=["symbol", "day"])
    out["B_fo_ban"] = {
        "entry_events": len(re), "exit_events": len(rx),
        "entry_open_close": _stat(re.oc, re.day, MIS_HURDLE),
        **{f"entry_open_to_close_d{k}": _stat(re[f"ofwd{k}"], re.day, CNC_HURDLE) for k in (1, 3, 5)},
        "exit_open_close": _stat(rx.oc, rx.day, MIS_HURDLE),
        **{f"exit_open_to_close_d{k}": _stat(rx[f"ofwd{k}"], rx.day, CNC_HURDLE) for k in (1, 3, 5)}}

    # D: overnight gaps
    out["D_gaps"] = {}
    for th in (2.0, 3.0):
        g = p[p.gap.abs() >= th]
        out["D_gaps"][f"abs_gap_ge_{th}"] = {"continuation_open_close_MIS": _stat(np.sign(g.gap) * g.oc, g.day, MIS_HURDLE),
                                              "up_gaps": int((g.gap > 0).sum()), "down_gaps": int((g.gap < 0).sum())}

    # E: extreme days, reversal
    x = p[p.cc.abs() >= 5.0]
    out["E_extreme_reversal"] = {"events": len(x)}
    for k in (1, 3, 5):
        out["E_extreme_reversal"][f"reversal_d{k}_both_sides"] = _stat(-np.sign(x.cc) * x[f"fwd{k}"], x.day, CNC_HURDLE)
        dn = x[x.cc <= -5.0]
        out["E_extreme_reversal"][f"long_after_down5_d{k}_CNC"] = _stat(dn[f"fwd{k}"], dn.day, CNC_HURDLE)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths

    ap = argparse.ArgumentParser(description="Exploratory edge feasibility scan (design window only)")
    ap.add_argument("--snapshot", required=True)
    args = ap.parse_args(argv)
    res = scan(Path(args.snapshot))
    out_dir = paths.ensure(paths.outputs_dir() / "edge_scan")
    f = out_dir / f"scan_{datetime.now(IST):%Y%m%d_%H%M%S}.json"
    f.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))
    print(f"written: {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
