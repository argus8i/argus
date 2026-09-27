"""
research/studies/pead.py
========================
PEAD_DRIFT_LONG: after a quarterly result that the market greets with a strong, high-volume up day, buy the next
open and hold 20 sessions (CNC). Design evidence: strategy_lab L3 with NSE announcement timestamps (T0089: alpha
+2.03% net of CNC, t 2.83 by month). v1 takes the result timestamps from BSE's official results filings
(Antigravity's harvest from api.bseindia.com, committed at e4e18d3 and pinned by SHA-256), because the NSE
announcements feed on disk has no results filings after 2024-11-14. The same source serves design and holdout.
On 2021-10 .. 2024-11 the BSE timestamps map to the same 'public from' session as NSE's for 96% of results.

Rules (v1):
  event   BSE results filings of a stock grouped into one result when they fall within 45 days of the group's
          first filing; the result is public from the EARLIEST filing's time. d0 = the first session in which it
          is public: filed at or after 15:30 -> the next session; otherwise that session (before 09:15 or during
          the session).
  signal  day-0 close-to-close return minus NIFTY 50's >= +3%, and day-0 volume >= 2 x the average of the 20
          sessions before d0; eligible on d0+1.
  entry   buy at the d0+1 open (pre-open auction order), 1 tick worse; CNC; Rs 38,000 notional at the worst
          admissible entry. The primary is a percent return (no R sizing).
  stop    disaster stop 10% below entry: a later session opening at or below it fills at that open - 1 tick; a
          touch fills at the stop - 1 tick.
  exit    the close of d0+20 (20 sessions held), 1 tick worse.
  costs   DhanFeeEngine CNC on both legs.
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from research.studies.strategy_lab import CNC, DESIGN, Panel, stats

IST = timezone(timedelta(hours=5, minutes=30))
GROUP_DAYS, JUMP_PCT, VOL_MULT, HOLD, STOP_PCT = 45, 3.0, 2.0, 20, 0.10
BSE_FILE = "shared/track2_liquid/antigravity_staging/events_backup/bse_quarterly_results_filings_2021_2026.json"
BSE_SHA256 = "3adefca93ca71b04aedd1d19752aa4e4743489b8bf39abf15193e1ece1de00df"


class ResultsSourceRefused(RuntimeError):
    pass


def load_results(path: Path, expected_sha256: str = BSE_SHA256) -> pd.DataFrame:
    """(symbol, public_at): one row per result, the earliest filing of each 45-day group. Refuses a file whose
    SHA-256 is not the pinned one."""
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if sha != expected_sha256:
        raise ResultsSourceRefused(f"{path.name}: sha256 {sha[:12]} is not the pinned {expected_sha256[:12]}")
    d = json.loads(raw)
    rows = [(sym, r["filing_timestamp"]) for sym, recs in d.items() for r in recs if r.get("filing_timestamp")]
    f = pd.DataFrame(rows, columns=["symbol", "ts"])
    f["t"] = pd.to_datetime(f.ts, format="ISO8601").dt.tz_localize("Asia/Kolkata")
    f = f.sort_values(["symbol", "t"])
    out = []
    for sym, g in f.groupby("symbol"):
        start = None
        for t in g.t:
            if start is None or (t - start) > pd.Timedelta(days=GROUP_DAYS):
                start = t
                out.append((sym, t))
    return pd.DataFrame(out, columns=["symbol", "public_at"])


def public_session(days: List[str], t: pd.Timestamp) -> Optional[int]:
    d, hm = t.date().isoformat(), t.hour * 60 + t.minute
    k = bisect.bisect_right(days, d) if hm >= 15 * 60 + 30 else bisect.bisect_left(days, d)
    return k if k < len(days) else None


def signals(P: Panel, results: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    cc = P.cc_mkt()
    va = P.vol_avg_prior(20)
    rows = []
    for sym, t in zip(results.symbol, results.public_at):
        j = P.j.get(sym)
        d0 = public_session(P.days, t)
        if j is None or d0 is None or d0 < 1 or d0 + HOLD >= len(P.days):
            continue
        if not (start <= P.days[d0] <= end):
            continue
        if cc[d0, j] >= JUMP_PCT and np.isfinite(va[d0, j]) and P.V[d0, j] >= VOL_MULT * va[d0, j] and P.elig[d0 + 1, j]:
            rows.append((sym, P.days[d0], d0, d0 + 1, d0 + HOLD, float(cc[d0, j]), t.isoformat()))
    return pd.DataFrame(rows, columns=["symbol", "d0", "signal", "entry", "exit", "day0_mkt_pct", "public_at"])


def simulate(P: Panel, sig: pd.DataFrame, stop_pct: float = STOP_PCT, slippage_ticks: int = 1,
             notional_rs: float = 38000.0) -> pd.DataFrame:
    from research.backtest.bars import round_to_tick, tick_size
    from research.backtest.cost_model import DhanFeeEngine, OrderSide, ProductType
    from research.backtest.engine import EngineConfig

    clamp = EngineConfig().clamp_pct
    out = []
    for r in sig.itertuples():
        j, a, b = P.j[r.symbol], int(r.entry), int(r.exit)
        ref = P.O[a, j]
        if not (np.isfinite(ref) and ref > 0):
            out.append({"disposition": "NO_OPEN"})
            continue
        ref = round_to_tick(float(ref), "up")
        tick = tick_size(ref)
        entry = ref + slippage_ticks * tick
        qty = int(notional_rs // (round_to_tick(ref * (1 + clamp), "up") + slippage_ticks * tick))
        if qty <= 0:
            out.append({"disposition": "ZERO_QTY"})
            continue
        stop = round_to_tick(entry * (1 - stop_pct), "down")
        px, reason = None, "TIME"
        for d in range(a, b + 1):
            o, lo = P.O[d, j], P.L[d, j]
            if not np.isfinite(lo):
                continue
            if d > a and o <= stop:
                px, reason = o - slippage_ticks * tick, "STOP_GAP"
                break
            if lo <= stop:
                px, reason = stop - slippage_ticks * tick, "STOP"
                break
        if px is None:
            c = P.C[b, j]
            if not np.isfinite(c):
                out.append({"disposition": "NO_EXIT_PRICE"})
                continue
            px = c - slippage_ticks * tick
        fees = (DhanFeeEngine.calculate_order(OrderSide.BUY, [(entry, qty)], ProductType.CNC).total_charges
                + DhanFeeEngine.calculate_order(OrderSide.SELL, [(px, qty)], ProductType.CNC).total_charges)
        pnl = (px - entry) * qty - fees
        out.append({"disposition": "FILLED", "qty": qty, "entry_px": entry, "stop_px": stop, "exit_px": px,
                    "exit_reason": reason, "fees_rs": fees, "net_pnl_rs": pnl, "net_pct": pnl / (entry * qty) * 100})
    return sig.reset_index(drop=True).join(pd.DataFrame(out))


def summarise(sim: pd.DataFrame) -> Dict[str, Any]:
    """Mean net percent return per trade, SE clustered by entry month (CR1): 20-session holds overlap within a
    month and share its market."""
    from research.decision.stats import clustered_se

    f = sim[sim.disposition == "FILLED"]
    months = f.d0.str[:7]
    v = f.net_pct.to_numpy(float)
    se = clustered_se(list(v), list(months)) if len(v) > 1 else math.nan
    t = float(v.mean() / se) if len(v) > 1 and math.isfinite(se) and se > 0 else None
    return {"signals": len(sim), "filled": len(f), "months": int(months.nunique()),
            "dispositions": sim.disposition.value_counts().to_dict(),
            "mean_net_pct": float(v.mean()) if len(v) else None, "se_cluster_by_month": se, "t_cluster": t,
            "median_net_pct": float(np.median(v)) if len(v) else None,
            "mean_net_pnl_rs": float(f.net_pnl_rs.mean()) if len(f) else None,
            "win_rate": float((f.net_pct > 0).mean()) if len(f) else None,
            "exit_reasons": f.exit_reason.value_counts().to_dict() if len(f) else {},
            "by_year": {y: {"n": len(g), "mean_net_pct": round(float(g.net_pct.mean()), 3)}
                        for y, g in f.groupby(f.d0.str[:4])}}


def drift_alpha(P: Panel, sig: pd.DataFrame) -> Dict[str, Any]:
    """Second gate: beta-adjusted open(d0+1) -> close(d0+20) return minus the 0.28% CNC hurdle."""
    ev = sig.assign(month=sig.d0.str[:7])[["symbol", "signal", "entry", "exit", "month"]]
    tr = P.trades(ev, "month")
    s = stats(tr, CNC)
    return {k: s.get(k) for k in ("n", "mean_raw", "mean_badj", "alpha_net", "t_alpha_net", "years")}


def evaluate(P: Panel, results: pd.DataFrame, start: str, end: str, **kw: Any) -> Dict[str, Any]:
    sig = signals(P, results, start, end)
    sim = simulate(P, sig, **kw)
    return {"summary": summarise(sim), "drift_alpha": drift_alpha(P, sig), "_sim": sim}


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths

    ap = argparse.ArgumentParser(description="PEAD_DRIFT_LONG design-set evaluation (BSE result timestamps)")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--start", default=DESIGN[0])
    ap.add_argument("--end", default=DESIGN[1])
    args = ap.parse_args(argv)
    if args.end > DESIGN[1]:
        print("REFUSED: design window only (the holdout is read once, after a locked pre-registration)")
        return 2
    snap = Path(args.snapshot)
    try:
        res_tab = load_results(paths.main_checkout() / BSE_FILE)
    except ResultsSourceRefused as exc:
        print(f"REFUSED: {exc}")
        return 2
    res_tab = res_tab[res_tab.public_at.dt.date.astype(str) <= args.end]           # nothing past the design end
    P = Panel(snap)
    out: Dict[str, Any] = {"window": [args.start, args.end], "snapshot": snap.name, "results_source_sha256": BSE_SHA256,
                           "results": len(res_tab), "variants": {}}
    out_dir = paths.ensure(paths.outputs_dir() / "pead" / f"{datetime.now(IST):%Y%m%d_%H%M%S}_design")
    for name, kw in {"primary": {}, "slippage_2_ticks": {"slippage_ticks": 2}}.items():
        r = evaluate(P, res_tab, args.start, args.end, **kw)
        r.pop("_sim").to_parquet(out_dir / f"signals_{name}.parquet", index=False)
        out["variants"][name] = r
    (out_dir / "result.json").write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps(out, indent=1, default=str))
    print(f"written: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
