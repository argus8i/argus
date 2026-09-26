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


ENTRY_MODELS = ("open", "first_minute_low", "vwap_5min")


class FirstMinutes:
    """The 09:15-09:19 one-minute candles of a symbol-day from the raw Upstox files (hash-verified history;
    the same vendor series as the 15-minute bars, so the same split/bonus scale)."""

    def __init__(self, raw_root: Path) -> None:
        import json as _json

        self.root = raw_root
        self.files: Dict[str, List[tuple]] = {}
        for line in (raw_root / "manifest.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = _json.loads(line)
                if r.get("interval") == "1minute" and r.get("status") == 200:
                    a, b = r["window"].split("_")
                    self.files.setdefault(r["symbol"], []).append((a, b, r["file"]))
        self._cache: Dict[str, Any] = {}

    def minutes(self, symbol: str, day: str) -> List[List[Any]]:
        import gzip
        import json as _json

        for a, b, f in self.files.get(symbol, []):
            if a <= day <= b:
                if f not in self._cache:
                    raw = (self.root / f).read_bytes()
                    self._cache = {f: _json.loads(gzip.decompress(raw))["data"]["candles"]}
                rows = [c for c in self._cache[f] if c[0][:10] == day and "09:15" <= c[0][11:16] <= "09:19"]
                return sorted(rows, key=lambda c: c[0])
        return []


def entry_price(model: str, open_0915: float, minutes: List[List[Any]]) -> Optional[float]:
    """The pre-slippage short-entry price: the 09:15 open, the LOW of the 09:15 minute (the worst price a short
    could have sold at in the first minute), or the 09:15-09:19 VWAP (typical price x volume)."""
    if model == "open":
        return open_0915
    if not minutes:
        return None
    if model == "first_minute_low":
        return float(minutes[0][3]) if minutes[0][0][11:16] == "09:15" else None
    vol = sum(float(m[5]) for m in minutes)
    if vol <= 0:
        return None
    return sum((float(m[2]) + float(m[3]) + float(m[4])) / 3 * float(m[5]) for m in minutes) / vol


def simulate(store: Any, events: pd.DataFrame, stop_pct: float, slippage_ticks: int = 1,
             entry_model: str = "open", first_minutes: Optional[FirstMinutes] = None) -> List[Dict[str, Any]]:
    import dataclasses

    from research.backtest.bars import Bar, round_to_tick, tick_size
    from research.backtest.engine import EngineConfig
    from research.studies.signal_sim import simulate_signal, size_qty

    if entry_model not in ENTRY_MODELS:
        raise ValueError(f"entry_model must be one of {ENTRY_MODELS}")
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
        mins = first_minutes.minutes(ev.symbol, ev.day) if (first_minutes and entry_model != "open") else []
        p = entry_price(entry_model, bars[0].open, mins)
        if p is None or not (p > 0):
            out.append({"symbol": ev.symbol, "day": ev.day, "disposition": "NO_ENTRY_PRICE"})
            continue
        p = round_to_tick(p, "down")                        # a short's price, rounded against us
        b0 = bars[0]
        bars = [dataclasses.replace(b0, open=p, high=max(b0.high, p), low=min(b0.low, p))] + list(bars[1:])
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
    ap.add_argument("--entry-model", choices=ENTRY_MODELS, default="open")
    ap.add_argument("--raw-upstox", default=None, help="raw/upstox root (default: <main history>/raw/upstox)")
    args = ap.parse_args(argv)
    if args.end > "2024-09-30":
        print("REFUSED: design window only (the holdout is read once, after a locked pre-registration)")
        return 2
    snap = paths.history_dir()
    store = ParquetCandleStore()
    ev = ban_entries(snap, args.start, args.end)
    fm = None
    if args.entry_model != "open":
        raw = Path(args.raw_upstox) if args.raw_upstox else \
            paths.main_checkout() / "shared" / "track2_liquid" / "history" / "raw" / "upstox"
        fm = FirstMinutes(raw)
    res = {"window": [args.start, args.end], "events": len(ev), "entry_model": args.entry_model, "variants": {}}
    out_dir = paths.ensure(paths.outputs_dir() / "ban_entry" /
                           f"{datetime.now(IST):%Y%m%d_%H%M%S}_{args.entry_model}")
    for s in (float(x) for x in args.stops.split(",")):
        rows = simulate(store, ev, s, entry_model=args.entry_model, first_minutes=fm)
        res["variants"][f"stop_{s:.0%}"] = summarise(rows)
        pd.DataFrame(rows).to_parquet(out_dir / f"signals_stop{int(s * 100)}.parquet", index=False)
    (out_dir / "result.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))
    print(f"written: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
