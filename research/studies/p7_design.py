"""
research/studies/p7_design.py
=============================
P7.2 design-set tasks (plan P7.2) on the design window of the pre-registration (data.design, amended by
Yashu to 2022-01-03 .. 2024-09-30). The holdout stays hidden by the HoldoutGuard; post-CAS sessions and every
session outside the design window are cut off by DesignWindowStore.

Tasks implemented here:
- zstar   (P7.2b) z* = 95th percentile of the per-stock-day maximum |Z_t| over the decision window
          (bar closes 10:00..13:30, t = 2..16), across all eligible design stock-days, BEFORE the volume,
          news and market filters. Computed by running the pre-registered ResidRevAdapter in SCAN mode through
          BacktestEngine, so the Z arithmetic is the adapter's own. No returns are used. Frozen to 2 dp.
- orbprod (P7.2.1) ORB_PROD R statistics and daily series on the design window (no parameters).
The holding-period study (P7.2c) needs the news filter (board meetings / corporate actions) and waits for
those NSE events.

Data-quality exclusions (P7.2.4, decided on design data only; research/data/validate.py table):
- special sessions: a design date whose session is structurally invalid for >= 50% of symbols
  (e.g. the Saturday special sessions 2024-03-02 and 2024-05-18);
- symbol-sessions marked invalid by validate.py.
Every run writes <outputs>/p7/<task>_<stamp>/ with result.json and manifest.json (plan rule 1.2.8).
Labels carried into every output: the universe flags (FO_BAN_HISTORY_MISSING etc.) and the data source.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import subprocess
import sys
import time as _time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

import numpy as np

from research.data import paths

IST = timezone(timedelta(hours=5, minutes=30))
SPECIAL_SESSION_SHARE = 0.5


class DesignWindowStore:
    """Read-only view of a store that lists only sessions in [start, end] and not excluded. Bars of other
    sessions are not listed, so neither the engine nor the calibration can use them."""

    def __init__(self, store: Any, start: date, end: date, excluded_days: Iterable[date] = (),
                 excluded_symbol_days: Iterable[Tuple[str, date]] = ()) -> None:
        self._s, self.start, self.end = store, start, end
        self.excluded_days = set(excluded_days)
        self.excluded_symbol_days = set(excluded_symbol_days)
        self.mode = getattr(store, "mode", "STRATEGY")

    def _ok(self, symbol: str, d: date) -> bool:
        return (self.start <= d <= self.end and d not in self.excluded_days
                and (symbol, d) not in self.excluded_symbol_days)

    @property
    def symbols(self) -> List[str]:
        return self._s.symbols

    def kind(self, symbol: str) -> str:
        return self._s.kind(symbol)

    def sessions(self, symbol: str) -> List[date]:
        return [d for d in self._s.sessions(symbol) if self._ok(symbol, d)]

    def bars(self, symbol: str, day: date):
        return self._s.bars(symbol, day) if self._ok(symbol, day) else []

    def session_arrays(self, symbol: str, day: date):
        return self._s.session_arrays(symbol, day) if self._ok(symbol, day) else None

    def daily(self, symbol: str):
        return [d for d in self._s.daily(symbol) if d.day <= self.end]

    def daily_before(self, symbol: str, day: date):
        return [d for d in self._s.daily_before(symbol, day) if d.day <= self.end]


def quality_exclusions(validity_rows: Sequence[Mapping[str, Any]], start: date, end: date,
                       share: float = SPECIAL_SESSION_SHARE) -> Tuple[Set[date], Set[Tuple[str, date]]]:
    """(special session dates, invalid symbol-sessions) inside [start, end], from validate.py rows."""
    per_day: Dict[date, List[bool]] = defaultdict(list)
    bad: Set[Tuple[str, date]] = set()
    for r in validity_rows:
        d = date.fromisoformat(str(r["session"])[:10])
        if not (start <= d <= end):
            continue
        per_day[d].append(bool(r["valid"]))
        if not r["valid"]:
            bad.add((str(r["symbol"]), d))
    special = {d for d, v in per_day.items() if v and (1 - sum(v) / len(v)) >= share}
    return special, bad


def zstar_from(scan_max: Mapping[Tuple[str, date], float], q: float = 95.0) -> Dict[str, Any]:
    """z* and its diagnostics from per stock-day maxima (finite values only)."""
    vals = np.array([v for v in scan_max.values() if isinstance(v, float) and math.isfinite(v)])
    if vals.size == 0:
        return {"n_stock_days": 0, "z_star": None}
    by_year: Dict[int, List[float]] = defaultdict(list)
    for (s, d), v in scan_max.items():
        if math.isfinite(v):
            by_year[d.year].append(v)
    return {"n_stock_days": int(vals.size), "n_symbols": len({s for s, _ in scan_max}),
            "n_sessions": len({d for _, d in scan_max}),
            "z_star_raw": float(np.percentile(vals, q)), "z_star": round(float(np.percentile(vals, q)), 2),
            "quantiles": {str(p): round(float(np.percentile(vals, p)), 4) for p in (50, 75, 90, 95, 97.5, 99)},
            "by_year_p95": {str(y): round(float(np.percentile(v, q)), 3) for y, v in sorted(by_year.items())},
            "gaussian_reference": 2.59}


def day_block_ci(scan_max: Mapping[Tuple[str, date], float], q: float = 95.0, reps: int = 1000,
                 seed: int = 20260925) -> Tuple[float, float]:
    """95% interval of the q-th percentile by resampling whole sessions (A.18 day-block bootstrap)."""
    by_day: Dict[date, List[float]] = defaultdict(list)
    for (s, d), v in scan_max.items():
        if math.isfinite(v):
            by_day[d].append(v)
    days = sorted(by_day)
    if len(days) < 2:
        return (math.nan, math.nan)
    arrs = [np.array(by_day[d]) for d in days]
    rng = np.random.default_rng(seed)
    stats = [np.percentile(np.concatenate([arrs[i] for i in rng.integers(0, len(days), len(days))]), q)
             for _ in range(reps)]
    return float(np.percentile(stats, 2.5)), float(np.percentile(stats, 97.5))


def _git_commit() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=paths.repo_root(), capture_output=True, text=True,
                              timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def manifest(task: str, spec_path: Path, extra: Mapping[str, Any], started: datetime) -> Dict[str, Any]:
    import pandas
    import pyarrow

    hist = paths.history_dir()
    data_manifest = hist / "manifests" / "upstox_v2.json"
    return {"task": task, "git_commit": _git_commit(), "prereg": str(spec_path),
            "prereg_sha256_lf": _sha256_file(spec_path),
            "data_manifest": str(data_manifest),
            "data_manifest_sha256": _sha256_file(data_manifest) if data_manifest.exists() else None,
            "universe_table_sha256": _sha256_file(paths.reference_dir() / "universe_daily.parquet"),
            "python": sys.version.split()[0], "platform": platform.platform(),
            "packages": {"numpy": np.__version__, "pandas": pandas.__version__, "pyarrow": pyarrow.__version__},
            "started": started.isoformat(timespec="seconds"),
            "finished": datetime.now(IST).isoformat(timespec="seconds"), **extra}


def _setup(limit_sessions: Optional[int] = None):
    import pandas as pd

    from research.data.sector_map import load_sector_map
    from research.data.store_parquet import ParquetCandleStore
    from research.data.universe_build import TableUniverse
    from research.studies import prereg_io

    spec_path = prereg_io.PREREG_DIR / "resid_rev_v1.yaml"
    spec = prereg_io.load(spec_path)
    start, end = (date.fromisoformat(str(x)) for x in spec["data"]["design"])
    validity = pd.read_parquet(paths.reference_dir() / "session_validity.parquet",
                               columns=["symbol", "session", "valid"]).to_dict("records")
    special, bad = quality_exclusions(validity, start, end)
    base = ParquetCandleStore()                                   # STRATEGY mode: holdout hidden
    if limit_sessions:
        days = sorted({d for s in base.symbols if base.kind(s) == "TRADABLE" for d in base.sessions(s)
                       if start <= d <= end})
        end = days[min(len(days), limit_sessions) - 1]
    store = DesignWindowStore(base, start, end, special, bad)
    universe = TableUniverse.from_parquet()
    return spec, spec_path, store, universe, load_sector_map(), (start, end), special, bad


def run_zstar(limit_sessions: Optional[int] = None) -> Tuple[Dict[str, Any], Dict[Tuple[str, date], float]]:
    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.features.calibration import CalibrationConfig, CalibrationProvider
    from research.features.events import NoEventsData
    from research.strategies.resid_rev import ResidRevAdapter

    t0 = _time.perf_counter()
    spec, spec_path, store, universe, sector_map, (start, end), special, bad = _setup(limit_sessions)
    adapter = ResidRevAdapter(spec, variant="RESID_REV", mode="SCAN")
    # calibrate exactly the universe-table symbols, so files added to the history later (or during a run)
    # cannot change the universe diurnal shape
    universe_symbols = sorted({k[0] for k in universe.table})
    provider = CalibrationProvider(store, sector_map, CalibrationConfig.from_prereg(spec), symbols=universe_symbols)
    eng_cfg = spec["engine"]
    cfg = EngineConfig(var_elm_rate=float(eng_cfg["var_elm_rate"]), allow_shorts=bool(eng_cfg["allow_shorts"]),
                       r_basis=str(eng_cfg["r_basis"]), stop_limit_offset_pct=0.005)
    res = BacktestEngine(store, universe, [adapter], cfg, calibration_provider=provider,
                         events_provider=NoEventsData()).run()
    scan = dict(adapter.scan_max)
    out = zstar_from(scan)
    out["day_block_ci95"] = day_block_ci(scan)
    out.update({"design_window": [start.isoformat(), end.isoformat()], "sessions_run": len(res.dates),
                "special_sessions_excluded": sorted(d.isoformat() for d in special),
                "invalid_symbol_sessions_excluded": len(bad),
                "reasons": dict(adapter.reasons), "universe_flags": list(universe.flags),
                "data_source": "UPSTOX_API_V2", "runtime_s": round(_time.perf_counter() - t0, 1),
                "label": "DESIGN_SET_SCAN_NO_RETURNS"})
    return out, scan


def run_orbprod(limit_sessions: Optional[int] = None) -> Dict[str, Any]:
    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.decision.stats import clustered_se
    from research.strategies.orb_prod import OrbProdAdapter

    t0 = _time.perf_counter()
    spec, spec_path, store, universe, sector_map, (start, end), special, bad = _setup(limit_sessions)
    adapter = OrbProdAdapter()
    cfg = EngineConfig(var_elm_rate=0.20, stop_limit_offset_pct=0.005, r_basis="stop_limit")
    res = BacktestEngine(store, universe, [adapter], cfg).run()
    sig = [s for s in res.signals if s.counterfactual_net_r is not None and math.isfinite(s.counterfactual_net_r)]
    net = np.array([s.counterfactual_net_r for s in sig])
    by_day: Dict[str, float] = defaultdict(float)
    for s in sig:
        by_day[s.signal_time.date().isoformat()] += s.counterfactual_net_r
    se = clustered_se(list(net), [s.signal_time.date() for s in sig]) if net.size > 1 else math.nan
    return {"strategy": "ORB_PROD", "design_window": [start.isoformat(), end.isoformat()],
            "sessions_run": len(res.dates), "signals": int(net.size), "signal_days": len(by_day),
            "mean_net_r": float(net.mean()) if net.size else None,
            "sd_net_r": float(net.std(ddof=1)) if net.size > 1 else None,
            "se_cluster_by_day": se if math.isfinite(se) else None,
            "t_cluster": float(net.mean() / se) if net.size > 1 and math.isfinite(se) and se > 0 else None,
            "exit_reasons": dict(Counter(s.counterfactual_exit_reason for s in sig)),
            "decision_reasons": dict(adapter.reasons), "daily_cf_r": dict(sorted(by_day.items())),
            "r_basis": "stop_limit", "evidence_class": "E1_CF (never admissible)",
            "universe_flags": list(universe.flags), "data_source": "UPSTOX_API_V2",
            "production_manifest": adapter.manifest(), "runtime_s": round(_time.perf_counter() - t0, 1),
            "label": "DESIGN_SET_BASELINE"}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="P7.2 design-set tasks")
    ap.add_argument("task", choices=["zstar", "orbprod"])
    ap.add_argument("--limit-sessions", type=int, default=None, help="first N design sessions only (timing)")
    ap.add_argument("--allow-live-history", action="store_true",
                    help="run on the live (mutable) shared history instead of a sealed snapshot; trials only")
    args = ap.parse_args(argv)
    from research.data import snapshot

    # fail closed: a design result must come from a sealed snapshot other agents cannot rewrite mid-run
    inputs = snapshot.describe()
    if inputs["kind"] != "SEALED_SNAPSHOT" and not args.allow_live_history:
        print(f"REFUSED: {paths.history_dir()} is not a sealed snapshot. Set TRACK2_HISTORY_DIR to one "
              "(python -m research.data.snapshot), or pass --allow-live-history for a trial run.")
        return 2
    universe_sha_start = _sha256_file(paths.reference_dir() / "universe_daily.parquet")
    started = datetime.now(IST)
    out_dir = paths.ensure(paths.outputs_dir() / "p7" / f"{args.task}_{started:%Y%m%d_%H%M%S}")
    from research.studies import prereg_io

    spec_path = prereg_io.PREREG_DIR / "resid_rev_v1.yaml"
    if args.task == "zstar":
        res, scan = run_zstar(args.limit_sessions)
        import pandas as pd

        pd.DataFrame([{"symbol": s, "session": d.isoformat(), "max_abs_z": v} for (s, d), v in scan.items()]) \
            .to_parquet(out_dir / "scan_max.parquet", index=False)
    else:
        res = run_orbprod(args.limit_sessions)
    (out_dir / "result.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps(manifest(args.task, spec_path,
                                                               {"limit_sessions": args.limit_sessions,
                                                                "seed": 20260925,
                                                                "history_inputs": inputs,
                                                                "history_inputs_after_run": snapshot.describe(),
                                                                "universe_table_sha256_at_start":
                                                                    universe_sha_start}, started),
                                                      indent=1, default=str), encoding="utf-8")
    brief = {k: v for k, v in res.items() if k not in ("daily_cf_r", "production_manifest")}
    print(json.dumps(brief, indent=1, default=str))
    print(f"written: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
