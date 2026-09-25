"""
research/data/cross_source.py
=============================
Cross-source proof (plan P3.9): compare the parquet history with an independent reference file on the
symbol-sessions they share. Both stores are opened in QA mode.

Per shared symbol-session:
- bar starts must be identical (an offset of one bar means a parser bug: stop and fix it first);
- OHLC must agree within max(2 ticks, 0.20%) (Yashu, 25 Sep 2026; the plan said one tick). Indices: 0.01%;
- volume must agree within 1%.
Every mismatch is listed. The reference today is the Kite 32-session file (8 stocks + NIFTY 50).

CLI:
    python -m research.data.cross_source --reference shared/track2_liquid/historical_candles_track2.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from research.backtest.bars import CandleStore, tick_size
from research.data.indices import canonical_index
from research.data.store_parquet import ParquetCandleStore


PRICE_TOL_TICKS = 2          # Yashu, 25 Sep 2026: multi-broker tolerance max(2 ticks, 0.20%) replaces
PRICE_TOL_PCT = 0.002        # the plan's one tick (P3.9). Volume stays within 1%; indices within 0.01%.
MIN_AGREEMENT = 0.995        # Yashu, 25 Sep 2026: each OHLC field of stocks within tolerance on >= 99.5% of bars
GATE_VOLUME = False          # Yashu, 25 Sep 2026: volume mismatches are reported, not gated


def compare(store: Any, reference: Any, vol_tol: float = 0.01, index_tol: float = 1e-4,
            price_tol_ticks: int = PRICE_TOL_TICKS, price_tol_pct: float = PRICE_TOL_PCT,
            min_agreement: float = MIN_AGREEMENT, gate_volume: bool = GATE_VOLUME) -> Dict[str, Any]:
    """Pass rule: at least one bar compared; identical bar starts in every shared session; for stocks, each
    of open/high/low/close within tolerance on >= min_agreement of bars; index series with no mismatch at
    all; volume within vol_tol on every bar only if gate_volume (otherwise reported). The plan's original
    rule is compare(..., price_tol_ticks=1, price_tol_pct=0, min_agreement=1.0, gate_volume=True)."""
    ref_names = {(canonical_index(s) or s): s for s in reference.symbols}
    shared = sorted(set(ref_names) & set(store.symbols))
    mismatches: List[Dict[str, Any]] = []
    counts: Counter = Counter()
    offsets = 0
    for sym in shared:
        kind = store.kind(sym)
        ref_sym = ref_names[sym]
        days = sorted(set(store.sessions(sym)) & set(reference.sessions(ref_sym)))
        for day in days:
            a = {b.start: b for b in store.bars(sym, day)}
            r = {b.start: b for b in reference.bars(ref_sym, day)}
            counts["sessions"] += 1
            if set(a) != set(r):
                only_a, only_r = sorted(set(a) - set(r)), sorted(set(r) - set(a))
                step = timedelta(minutes=15)
                if {t + step for t in a} == set(r) or {t - step for t in a} == set(r):
                    offsets += 1
                mismatches.append({"symbol": sym, "session": day.isoformat(), "field": "starts",
                                   "only_store": [t.strftime("%H:%M") for t in only_a],
                                   "only_reference": [t.strftime("%H:%M") for t in only_r]})
            for t in sorted(set(a) & set(r)):
                x, y = a[t], r[t]
                counts["bars"] += 1
                counts["stock_bars" if kind == "TRADABLE" else "index_bars"] += 1
                for f in ("open", "high", "low", "close"):
                    px, py = getattr(x, f), getattr(y, f)
                    tol = (max(price_tol_ticks * tick_size(py), price_tol_pct * abs(py)) + 1e-9
                           if kind == "TRADABLE" else abs(py) * index_tol)
                    if abs(px - py) > tol:
                        counts[f"{f}_mismatch"] += 1
                        counts[f"{'stock' if kind == 'TRADABLE' else 'index'}_{f}_mismatch"] += 1
                        mismatches.append({"symbol": sym, "session": day.isoformat(), "start": t.strftime("%H:%M"),
                                           "field": f, "store": px, "reference": py,
                                           "diff_bps": round((px - py) / py * 1e4, 2)})
                if kind == "TRADABLE":
                    if y.volume > 0 and abs(x.volume - y.volume) / y.volume > vol_tol:
                        counts["volume_mismatch"] += 1
                        mismatches.append({"symbol": sym, "session": day.isoformat(), "start": t.strftime("%H:%M"),
                                           "field": "volume", "store": x.volume, "reference": y.volume,
                                           "ratio": round(x.volume / y.volume, 4)})
    bars = counts["bars"] or 1
    fields = ("open", "high", "low", "close")
    stock_agreement = {f: (1.0 - counts[f"stock_{f}_mismatch"] / counts["stock_bars"]) if counts["stock_bars"] else None
                       for f in fields}
    passed = (counts["bars"] > 0 and offsets == 0
              and not any(m["field"] == "starts" for m in mismatches)
              and all(a is None or a >= min_agreement - 1e-12 for a in stock_agreement.values())
              and not any(counts[f"index_{f}_mismatch"] for f in fields)
              and (counts["volume_mismatch"] == 0 or not gate_volume))
    return {
        "tolerances": {"price": f"max({price_tol_ticks} ticks, {price_tol_pct:.2%})", "volume": f"{vol_tol:.0%}",
                       "index": f"{index_tol:.2%}", "min_agreement": min_agreement, "gate_volume": gate_volume},
        "stock_field_agreement": stock_agreement,
        "shared_symbols": shared,
        "shared_sessions": counts["sessions"],
        "shared_bars": counts["bars"],
        "start_mismatch_sessions": sum(1 for m in mismatches if m["field"] == "starts"),
        "one_bar_offset_sessions": offsets,
        "price_mismatch_bars": {f: counts[f"{f}_mismatch"] for f in ("open", "high", "low", "close")},
        "price_mismatch_share": {f: counts[f"{f}_mismatch"] / bars for f in ("open", "high", "low", "close")},
        "volume_mismatch_bars": counts["volume_mismatch"],
        "volume_mismatch_share": counts["volume_mismatch"] / bars,
        "passed": passed,                                   # rule: see the docstring
        "mismatches": mismatches,
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Cross-source proof (plan P3.9)")
    ap.add_argument("--reference", required=True)
    ap.add_argument("--root", default=None)
    ap.add_argument("--out", default=None, help="write the full result (with every mismatch) as JSON")
    args = ap.parse_args(argv)
    store = ParquetCandleStore(args.root, mode="QA")
    ref = CandleStore.from_historical_json(args.reference, mode="QA")
    res = compare(store, ref)
    brief = {k: v for k, v in res.items() if k != "mismatches"}
    brief["mismatch_examples"] = res["mismatches"][:15]
    print(json.dumps(brief, indent=1, default=str))
    if args.out:
        Path(args.out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
