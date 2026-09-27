"""
research/studies/expiry_relief.py
=================================
EXPIRY_RELIEF_LONG: after a monthly stock-futures expiry, buy the F&O stocks that fell >= 5% over the 20
sessions into it; hold at most 5 sessions (CNC delivery). Proposed by Antigravity (26 Sep blueprint); reproduced
and stress-tested by Claude on the design window (strategy_lab L1 / L1D, trials T0082-T0104).

Mechanism (hypothesis): stock futures and in-the-money stock options settle by physical delivery (SEBI, 2018-19
rollout), and delivery margins on open positions step up over the sessions before expiry, so leveraged holders
who cannot take or give delivery close out into the expiry. For stocks already falling, that close-out adds
selling; once the expiry has passed the pressure is gone and the price recovers. Design evidence: the effect
peaks for a signal on the expiry session itself and decays for signals 1-3 sessions either side; the same filter
on non-expiry sessions loses money.

Rules (v1):
  signal  E = a monthly stock-futures expiry session: the nearest futures expiry of most F&O stocks on that
          session (point-in-time F&O table). close_E / close_(E-20) - 1 <= -5%; the stock is eligible on E+1
          (Track 2 universe: point-in-time F&O member, lagged price >= Rs 10, DTV20 >= Rs 30 Cr).
  entry   buy at the E+1 open (a pre-open auction order: the day's official open is the auction price),
          `slippage_ticks` worse; CNC.
  stop    SL-M sell at round_down(entry x 0.97). A later session OPENING at or below it fills at that open
          (minus slippage); a touch fills at the stop minus slippage. A session touching stop and target: stop first.
  target  half the quantity at round_up(entry + 1.5 R) (a resting limit; a later session opening above it fills
          at that open).
  exit    the rest at the close of E+5 (the 5th session held), minus slippage.
  size    Adjusted A1: Rs 1,500 to the stop, Rs 38,000 at the worst admissible entry; DhanFeeEngine CNC on every
          leg. R = qty x (entry - stop).
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from research.studies.strategy_lab import CNC, DESIGN, Panel, expiry_sessions, stats

IST = timezone(timedelta(hours=5, minutes=30))
DROP_PCT, LOOKBACK, HOLD = -5.0, 20, 5


def signals(P: Panel, snap: Path, start: str, end: str) -> pd.DataFrame:
    """One row per (stock, expiry E) with E in [start, end]: symbol, expiry, signal (E), entry (E+1), exit (E+5),
    r20 (percent). Rows whose exit session is outside the panel are dropped (nothing past the panel is read)."""
    r20 = P.ret(LOOKBACK)
    rows = []
    for e in expiry_sessions(snap, P):
        d = P.days[e]
        if not (start <= d <= end) or e + HOLD >= len(P.days):
            continue
        for j in np.where(r20[e] <= DROP_PCT)[0]:
            if P.elig[e + 1, j]:
                rows.append((P.syms[j], d, e, e + 1, e + HOLD, float(r20[e, j])))
    return pd.DataFrame(rows, columns=["symbol", "expiry", "signal", "entry", "exit", "r20"])


def simulate_long(P: Panel, sig: pd.DataFrame, stop_pct: float = 0.03, target_r: Optional[float] = 1.5,
                  slippage_ticks: int = 1) -> pd.DataFrame:
    from research.backtest.bars import round_to_tick, tick_size
    from research.backtest.cost_model import DhanFeeEngine, OrderSide, ProductType
    from research.backtest.engine import EngineConfig
    from research.studies.signal_sim import size_qty

    ecfg = EngineConfig()
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
        stop = round_to_tick(entry * (1 - stop_pct), "down")
        tgt = round_to_tick(entry + target_r * (entry - stop), "up") if target_r else None
        cap_px = round_to_tick(ref * (1 + ecfg.clamp_pct), "up") + slippage_ticks * tick
        qty = size_qty(entry, stop, "BUY", ecfg.risk_budget_rs, ecfg.slot_cap_rs, None, notional_px=cap_px)
        if qty <= 0:
            out.append({"disposition": "ZERO_QTY"})
            continue
        half = qty // 2 if tgt is not None else 0
        legs: List[tuple] = []                            # (price, qty, ideal price, reason)
        left, reason = qty, "TIME"
        for d in range(a, b + 1):
            o, h, lo = P.O[d, j], P.H[d, j], P.L[d, j]
            if not np.isfinite(lo):
                continue
            if d > a and o <= stop:
                legs.append((o - slippage_ticks * tick, left, o, "STOP_GAP"))
                left, reason = 0, "STOP_GAP"
                break
            if lo <= stop:
                legs.append((stop - slippage_ticks * tick, left, stop, "STOP"))
                left, reason = 0, "STOP"
                break
            if half and left == qty and h >= tgt:
                px = o if (d > a and o >= tgt) else tgt
                legs.append((px, half, px, "TARGET"))
                left -= half
        if left:
            c = P.C[b, j]
            if not np.isfinite(c):
                out.append({"disposition": "NO_EXIT_PRICE"})
                continue
            legs.append((c - slippage_ticks * tick, left, c, "TIME"))
        fees = DhanFeeEngine.calculate_order(OrderSide.BUY, [(entry, qty)], ProductType.CNC).total_charges
        fees += sum(DhanFeeEngine.calculate_order(OrderSide.SELL, [(p, q)], ProductType.CNC).total_charges
                    for p, q, _, _ in legs)
        gross = sum((p - entry) * q for p, q, _, _ in legs)
        gross_ideal = sum((ip - ref) * q for _, q, ip, _ in legs)
        risk = qty * (entry - stop)
        out.append({"disposition": "FILLED", "qty": qty, "entry_px": entry, "stop_px": stop, "target_px": tgt,
                    "exit_reason": reason, "half_target": any(x[3] == "TARGET" for x in legs),
                    "gross_r": gross_ideal / risk, "slip_r": (gross_ideal - gross) / risk, "fee_r": fees / risk,
                    "net_r": (gross - fees) / risk, "net_pnl_rs": gross - fees, "risk_rs": risk,
                    "notional_rs": entry * qty})
    return sig.reset_index(drop=True).join(pd.DataFrame(out))


def summarise(sim: pd.DataFrame) -> Dict[str, Any]:
    """Mean net R over FILLED signals, SE clustered by expiry (CR1): trades on one expiry share one market."""
    from research.decision.stats import clustered_se

    f = sim[sim.disposition == "FILLED"]
    v = f.net_r.to_numpy(float)
    se = clustered_se(list(v), list(f.expiry)) if len(v) > 1 else math.nan
    t = float(v.mean() / se) if len(v) > 1 and math.isfinite(se) and se > 0 else None
    return {"signals": len(sim), "filled": len(f), "expiries": int(f.expiry.nunique()),
            "dispositions": sim.disposition.value_counts().to_dict(),
            "mean_net_r": float(v.mean()) if len(v) else None, "se_cluster_by_expiry": se, "t_cluster": t,
            "mean_gross_r": float(f.gross_r.mean()) if len(f) else None,
            "mean_fee_r": float(f.fee_r.mean()) if len(f) else None,
            "mean_slip_r": float(f.slip_r.mean()) if len(f) else None,
            "mean_net_pnl_rs": float(f.net_pnl_rs.mean()) if len(f) else None,
            "win_rate": float((f.net_r > 0).mean()) if len(f) else None,
            "exit_reasons": f.exit_reason.value_counts().to_dict() if len(f) else {},
            "by_year": {y: {"n": len(g), "mean_net_r": round(float(g.net_r.mean()), 4)}
                        for y, g in f.groupby(f.expiry.str[:4])}}


def drift_alpha(P: Panel, sig: pd.DataFrame) -> Dict[str, Any]:
    """The gate against pure beta: beta-adjusted open(E+1) -> close(E+5) return minus the CNC hurdle."""
    tr = P.trades(sig[["symbol", "signal", "entry", "exit", "expiry"]], "expiry")
    s = stats(tr, CNC)
    return {k: s.get(k) for k in ("n", "mean_raw", "mean_badj", "alpha_net", "t_alpha_net", "years")}


def evaluate(P: Panel, snap: Path, start: str, end: str, slippage_ticks: int = 1, stop_pct: float = 0.03,
             target_r: Optional[float] = 1.5, capacity: Optional[int] = None) -> Dict[str, Any]:
    sig = signals(P, snap, start, end)
    if capacity:
        sig = sig.sort_values(["expiry", "r20", "symbol"]).groupby("expiry").head(capacity).reset_index(drop=True)
    sim = simulate_long(P, sig, stop_pct, target_r, slippage_ticks)
    return {"summary": summarise(sim), "drift_alpha": drift_alpha(P, sig), "_sim": sim}


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths

    ap = argparse.ArgumentParser(description="EXPIRY_RELIEF_LONG design-set evaluation")
    ap.add_argument("--snapshot", required=True)
    ap.add_argument("--start", default=DESIGN[0])
    ap.add_argument("--end", default=DESIGN[1])
    args = ap.parse_args(argv)
    if args.end > DESIGN[1]:
        print("REFUSED: design window only (the holdout is read once, after a locked pre-registration)")
        return 2
    snap = Path(args.snapshot)
    P = Panel(snap)
    res: Dict[str, Any] = {"window": [args.start, args.end], "snapshot": snap.name, "variants": {}}
    out_dir = paths.ensure(paths.outputs_dir() / "expiry_relief" / f"{datetime.now(IST):%Y%m%d_%H%M%S}_design")
    for name, kw in {"primary": {}, "slippage_2_ticks": {"slippage_ticks": 2},
                     "capacity_3_most_oversold": {"capacity": 3}, "stop_5pct_no_target": {"stop_pct": 0.05,
                                                                                          "target_r": None}}.items():
        r = evaluate(P, snap, args.start, args.end, **kw)
        r.pop("_sim").to_parquet(out_dir / f"signals_{name}.parquet", index=False)
        res["variants"][name] = r
    (out_dir / "result.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(json.dumps(res, indent=1, default=str))
    print(f"written: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
