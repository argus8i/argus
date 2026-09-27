"""
research/studies/run_descriptive.py
===================================
Descriptive run of one strategy over the parquet history in STRATEGY mode (the holdout stays hidden).
It exists for P5 plumbing checks and the runtime measurement deferred from P2; it chooses no parameter and
its output is never evidence for the locked gate:
- every trade is bar-modelled (E1 / E1_CF);
- with today's data every visible session is post-CAS, which the pre-registration marks "descriptive only".
Since provenance enforcement (research/data/provenance.py) the store refuses QA_ONLY history, so this runner
no longer runs on the Yahoo-derived parquet; the 25 Sep outputs it produced from that data are registered
as trials T0022/T0023 and are not evidence.

CLI:
    python -m research.studies.run_descriptive --strategy ORB_PROD
    python -m research.studies.run_descriptive --strategy RESID_REV_SCAN
Output: research/outputs/descriptive/<strategy>_<timestamp>.json (gitignored)
"""
from __future__ import annotations

import argparse
import json
import math
import time as _time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from research.data import paths


def run(strategy: str, root: Optional[str] = None, universe_path: Optional[str] = None,
        sector_map_path: Optional[str] = None, industries_path: Optional[str] = None) -> Dict[str, Any]:
    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.data.sector_map import load_industries, load_sector_map
    from research.data.store_parquet import ParquetCandleStore
    from research.data.universe_build import TableUniverse
    from research.features.calibration import CalibrationConfig, CalibrationProvider
    from research.features.events import NoEventsData
    from research.studies import prereg_io

    t0 = _time.perf_counter()
    store = ParquetCandleStore(root)
    universe = TableUniverse.from_parquet(universe_path)
    industries = load_industries(industries_path or paths.shared_input("sector_mapping.json"))
    spec = prereg_io.load(prereg_io.PREREG_DIR / "resid_rev_v1.yaml")
    cfg = EngineConfig(var_elm_rate=0.20, allow_shorts=(strategy != "ORB_PROD"), stop_limit_offset_pct=0.005,
                       r_basis="stop_limit")
    provider = None
    if strategy == "ORB_PROD":
        from research.strategies.orb_prod import OrbProdAdapter

        adapter = OrbProdAdapter()
        manifest = adapter.manifest()
    elif strategy == "RESID_REV_SCAN":
        from research.strategies.resid_rev import ResidRevAdapter

        adapter = ResidRevAdapter(spec, variant="RESID_REV_NF", mode="SCAN")
        provider = CalibrationProvider(store, load_sector_map(sector_map_path), CalibrationConfig.from_prereg(spec))
        manifest = {}
    else:
        raise ValueError("strategy must be ORB_PROD or RESID_REV_SCAN")
    t_load = _time.perf_counter() - t0
    eng = BacktestEngine(store, universe, [adapter], cfg, sectors=industries, calibration_provider=provider,
                         events_provider=NoEventsData())
    res = eng.run()
    t_run = _time.perf_counter() - t0 - t_load
    sig = res.signals
    cf = np.array([s.counterfactual_net_r for s in sig if s.counterfactual_net_r is not None
                   and math.isfinite(s.counterfactual_net_r)])
    by_day: Dict[str, float] = {}
    for s in sig:
        r = s.counterfactual_net_r
        if r is not None and math.isfinite(r):
            by_day[s.signal_time.date().isoformat()] = by_day.get(s.signal_time.date().isoformat(), 0.0) + r
    out = {
        "strategy": strategy, "label": "DESCRIPTIVE_POST_CAS_E1_CF_NOT_EVIDENCE",
        "data_source": "parquet history (see manifests/)", "sessions": len(res.dates),
        "first_session": res.dates[0].isoformat() if res.dates else None,
        "last_session": res.dates[-1].isoformat() if res.dates else None,
        "decision_counts": res.decision_counts, "reasons": dict(Counter(getattr(adapter, "reasons", {}))),
        "signals": len(sig), "signal_days": len(by_day),
        "counterfactual_net_r": {
            "n": int(cf.size), "mean": float(cf.mean()) if cf.size else None,
            "sd": float(cf.std(ddof=1)) if cf.size > 1 else None,
            "se_naive": float(cf.std(ddof=1) / math.sqrt(cf.size)) if cf.size > 1 else None,
        },
        "counterfactual_exit_reasons": dict(Counter(s.counterfactual_exit_reason for s in sig)),
        "allocated_trades": len(res.trades), "dispositions": res.disposition_counts,
        "runtime_seconds": {"load": round(t_load, 2), "run": round(t_run, 2),
                            "per_session": round(t_run / max(1, len(res.dates)), 3)},
        "symbols_in_universe_table": len({k[0] for k in universe.table}), "universe_flags": list(universe.flags),
        "production_manifest": manifest,
    }
    if strategy == "RESID_REV_SCAN":
        out["scan_stock_days"] = len(adapter.scan_max)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Descriptive strategy run (not evidence)")
    ap.add_argument("--strategy", required=True, choices=["ORB_PROD", "RESID_REV_SCAN"])
    ap.add_argument("--root", default=None)
    ap.add_argument("--industries", default=None)
    args = ap.parse_args(argv)
    out = run(args.strategy, args.root, industries_path=args.industries)
    d = paths.ensure(paths.outputs_dir() / "descriptive")
    f = d / f"{args.strategy}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    f.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    print(json.dumps(out, indent=1, default=str))
    print(f"written: {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
