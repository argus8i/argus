"""
research/data/validate.py
=========================
Data-quality checks per symbol-session and a coverage summary (plan P3.8). Runs on a QA-mode store,
so every read is logged and none of it can reach strategy code.

A session is VALID only if all of these hold:
- the bar count equals the expected count for its instrument class and date (session_shape);
- the first bar starts at 09:15 and the slots are contiguous;
- every bar's OHLC is consistent and prices are positive and finite;
- (stocks) no bar has zero or missing volume. Yahoo writes missing volume as 0, and a 15-minute bar with
  no trades does not happen in a liquid F&O stock, so a zero is treated as a data gap.
Flags that do not invalidate a session: a bar return above 20%; intraday range outside the daily bar;
the intraday-volume / daily-volume ratio (reported as a distribution).

CLI:
    python -m research.data.validate [--root HISTORY_DIR] [--report research/notes/p3_validation.json]
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from research.backtest.bars import tick_size
from research.data import paths
from research.data.session_shape import CAS_START, expected_bar_count, slot_of
from research.data.store_parquet import ParquetCandleStore

BIG_RETURN = 0.20


def validate_session(kind: str, day: date, arr: Dict[str, np.ndarray],
                     daily_row: Optional[Any] = None) -> Dict[str, Any]:
    from datetime import datetime, timedelta, timezone

    ist = timezone(timedelta(hours=5, minutes=30))
    n = len(arr["close"])
    exp = expected_bar_count(kind, day)
    slots = [slot_of(datetime.fromtimestamp(int(e), ist).time()) for e in arr["start_epoch"]]
    reasons: List[str] = []
    if n != exp:
        reasons.append(f"BAR_COUNT_{n}_EXPECTED_{exp}")
    if not slots or slots[0] != 0:
        reasons.append("FIRST_BAR_NOT_0915")
    if any(s is None for s in slots) or any(b - a != 1 for a, b in zip(slots, slots[1:]) if a is not None and b is not None):
        reasons.append("NON_CONTIGUOUS")
    o, h, l, c, v = arr["open"], arr["high"], arr["low"], arr["close"], arr["volume"]
    finite = np.isfinite(o) & np.isfinite(h) & np.isfinite(l) & np.isfinite(c)
    ohlc_ok = finite & (o > 0) & (c > 0) & (l > 0) & (h >= np.maximum(o, c)) & (l <= np.minimum(o, c)) & (h >= l)
    if not bool(ohlc_ok.all()):
        reasons.append("OHLC_INCONSISTENT")
    zero_vol = int((v == 0).sum())
    missing_vol = int((v < 0).sum())
    if kind == "TRADABLE" and (zero_vol or missing_vol):
        reasons.append("VOLUME_GAP")
    rets = np.abs(np.diff(np.log(c))) if n > 1 and bool((c > 0).all()) else np.zeros(0)
    big = int((rets > math.log(1 + BIG_RETURN)).sum())
    out: Dict[str, Any] = {"n_bars": n, "expected": exp, "zero_vol_bars": zero_vol, "missing_vol_bars": missing_vol,
                           "big_return_bars": big, "valid": not reasons, "reasons": ";".join(reasons)}
    if daily_row is not None and n:
        tick = tick_size(float(daily_row.close)) if kind == "TRADABLE" else 0.0
        tol = max(tick, 1e-9)
        out["intraday_high_minus_daily"] = float(h.max() - daily_row.high)
        out["intraday_low_minus_daily"] = float(l.min() - daily_row.low)
        out["range_contained"] = bool(h.max() <= daily_row.high + tol and l.min() >= daily_row.low - tol)
        out["hl_equal_within_tick"] = bool(abs(h.max() - daily_row.high) <= tol and abs(l.min() - daily_row.low) <= tol)
        out["vol_ratio"] = float(v[v > 0].sum() / daily_row.volume) if daily_row.volume > 0 else float("nan")
    return out


def validate_store(store: ParquetCandleStore) -> List[Dict[str, Any]]:
    if store.mode != "QA":
        raise ValueError("validate_store needs a QA-mode store (it must see every session)")
    rows: List[Dict[str, Any]] = []
    for sym in store.symbols:
        kind = store.kind(sym)
        daily = {d.day: d for d in store.daily(sym)}
        for day in store.sessions(sym):
            arr = store.session_arrays(sym, day)
            if arr is None:
                continue
            r = validate_session(kind, day, arr, daily.get(day))
            r.update({"symbol": sym, "kind": kind, "session": day.isoformat(), "post_cas": day >= CAS_START,
                      "has_daily": day in daily})
            rows.append(r)
    return rows


def summarise(rows: List[Dict[str, Any]], min_valid_frac: float = 0.95) -> Dict[str, Any]:
    by_sym: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_sym.setdefault(r["symbol"], []).append(r)
    tradables = {s: v for s, v in by_sym.items() if v[0]["kind"] == "TRADABLE"}
    frac = {s: sum(x["valid"] for x in v) / len(v) for s, v in tradables.items()}
    passing = [s for s, f in frac.items() if f >= min_valid_frac]
    reasons = Counter()
    for r in rows:
        for x in filter(None, r["reasons"].split(";")):
            reasons[x.split("_EXPECTED")[0] if x.startswith("BAR_COUNT") else x] += 1
    ratios = [r["vol_ratio"] for r in rows if r["kind"] == "TRADABLE" and isinstance(r.get("vol_ratio"), float)
              and math.isfinite(r["vol_ratio"])]
    contained = [r["range_contained"] for r in rows if r["kind"] == "TRADABLE" and "range_contained" in r]
    equal = [r["hl_equal_within_tick"] for r in rows if r["kind"] == "TRADABLE" and "hl_equal_within_tick" in r]
    sessions = sorted({r["session"] for r in rows})
    return {
        "symbols": len(by_sym), "tradables": len(tradables), "sessions": len(sessions),
        "first_session": sessions[0] if sessions else None, "last_session": sessions[-1] if sessions else None,
        "pre_cas_sessions": sum(1 for s in sessions if s < CAS_START.isoformat()),
        "symbol_sessions": len(rows), "valid_symbol_sessions": sum(r["valid"] for r in rows),
        "tradables_with_valid_frac_ge": {"threshold": min_valid_frac, "count": len(passing),
                                         "share": len(passing) / len(tradables) if tradables else 0.0},
        "invalid_reasons": dict(reasons.most_common()),
        "worst_symbols": sorted(((round(f, 3), s) for s, f in frac.items()))[:10],
        "zero_volume_bars_tradable": sum(r["zero_vol_bars"] for r in rows if r["kind"] == "TRADABLE"),
        "big_return_bars": sum(r["big_return_bars"] for r in rows),
        "daily_vs_intraday": {
            "sessions_compared": len(contained),
            "range_contained_share": (sum(contained) / len(contained)) if contained else None,
            "hl_equal_within_tick_share": (sum(equal) / len(equal)) if equal else None,
            "vol_ratio_quantiles": ({q: float(np.quantile(ratios, q)) for q in (0.05, 0.25, 0.5, 0.75, 0.95)}
                                    if ratios else None),
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Validate the parquet history (plan P3.8)")
    ap.add_argument("--root", default=None)
    ap.add_argument("--report", default=None, help="write the summary JSON here")
    ap.add_argument("--table", default=None, help="write per symbol-session rows (parquet)")
    args = ap.parse_args(argv)
    store = ParquetCandleStore(args.root, mode="QA")
    rows = validate_store(store)
    summary = summarise(rows)
    summary["qa_reads_logged"] = len(store.guard.qa_log)
    text = json.dumps(summary, indent=1, default=str)
    print(text)
    if args.report:
        Path(args.report).write_text(text, encoding="utf-8")
    table = Path(args.table) if args.table else paths.ensure(paths.reference_dir()) / "session_validity.parquet"
    import pandas as pd

    pd.DataFrame(rows).to_parquet(table, index=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
