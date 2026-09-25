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
- hold    (P7.2c) one full-rule RESID_REV run at hold_bars h in {4, 8, EOD}, with z* taken from a zstar
          result computed on the same sealed snapshot. Reports mean net R (unconstrained per-signal
          counterfactuals) and the event study of the residual reversion curve at h in {1, 2, 4, 8, EOD}
          over the first h-independent candidate of each stock-day. The news filter needs board meetings
          and corporate actions for the whole window; until they are on disk the run is RESID_REV_NF.

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


def _git_dirty_research() -> List[str]:
    """Uncommitted changes under research/ (the code a run imports)."""
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", "research"], cwd=paths.repo_root(),
                             capture_output=True, text=True, timeout=20).stdout
        return [ln for ln in out.splitlines() if ln.strip()]
    except (OSError, subprocess.SubprocessError):
        return ["unknown"]


def _sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def manifest(task: str, spec_path: Path, extra: Mapping[str, Any], started: datetime) -> Dict[str, Any]:
    import pandas
    import pyarrow

    hist = paths.history_dir()
    data_manifest = hist / "manifests" / "upstox_v2.json"
    return {"task": task, "git_commit_at_end": _git_commit(), "prereg": str(spec_path),
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


def _cf_summary(signals: Sequence[Any]) -> Dict[str, Any]:
    """Unconstrained per-signal counterfactual statistics (plan D8, D15): every emitted signal simulated
    with identical fill rules; SE clustered by session (CR1). Also the gross / fee / slippage split and a
    per-signal table (written to signals.parquet by main)."""
    from research.decision.stats import clustered_se

    sig = [s for s in signals if s.counterfactual_net_r is not None and math.isfinite(s.counterfactual_net_r)]
    net = np.array([s.counterfactual_net_r for s in sig])
    by_day: Dict[str, float] = defaultdict(float)
    for s in sig:
        by_day[s.signal_time.date().isoformat()] += s.counterfactual_net_r
    se = clustered_se(list(net), [s.signal_time.date() for s in sig]) if net.size > 1 else math.nan

    def _mean(attr: str) -> Optional[float]:
        v = np.array([getattr(s, attr) for s in sig if getattr(s, attr) is not None], dtype=float)
        v = v[np.isfinite(v)]
        return float(v.mean()) if v.size else None

    table = [{"symbol": s.symbol, "signal_time": s.signal_time.isoformat(), "side": s.side,
              "entry_ref": s.entry_ref, "stop_loss": s.stop_loss, "max_bars": s.max_bars,
              "net_r": s.counterfactual_net_r, "gross_r": s.counterfactual_gross_r,
              "fee_r": s.counterfactual_fee_r, "slip_r": s.counterfactual_slip_r,
              "exit_reason": s.counterfactual_exit_reason, "disposition": s.disposition,
              "diagnostics": json.dumps(s.diagnostics or {}, default=str, sort_keys=True)} for s in sig]
    return {"signals": int(net.size), "signal_days": len(by_day),
            "mean_net_r": float(net.mean()) if net.size else None,
            "mean_gross_r": _mean("counterfactual_gross_r"), "mean_fee_r": _mean("counterfactual_fee_r"),
            "mean_slip_r": _mean("counterfactual_slip_r"),
            "sd_net_r": float(net.std(ddof=1)) if net.size > 1 else None,
            "se_cluster_by_day": se if math.isfinite(se) else None,
            "t_cluster": float(net.mean() / se) if net.size > 1 and math.isfinite(se) and se > 0 else None,
            "exit_reasons": dict(Counter(s.counterfactual_exit_reason for s in sig)),
            "daily_cf_r": dict(sorted(by_day.items())), "signals_table": table}


def _portfolio_summary(res: Any) -> Dict[str, Any]:
    """The engine's allocated trades (Adjusted A1 slots, aggregate cap, sector/cluster limits as configured):
    what a 3-slot book would actually have held, as opposed to the unconstrained per-signal statistics."""
    trades = [t for t in res.trades if math.isfinite(getattr(t, "net_r", math.nan))]
    daily = np.array([res.daily_pnl[d] for d in res.dates], dtype=float) if res.dates else np.zeros(0)
    eq = np.cumsum(daily) if daily.size else np.zeros(0)
    dd = float(np.max(np.maximum.accumulate(eq) - eq)) if eq.size else 0.0
    net_r = np.array([t.net_r for t in trades], dtype=float)
    return {"trades": len(trades), "net_pnl_rs": float(daily.sum()), "mean_trade_net_r": float(net_r.mean())
            if net_r.size else None, "win_rate": float(np.mean(net_r > 0)) if net_r.size else None,
            "daily_pnl_sd_rs": float(daily.std(ddof=1)) if daily.size > 1 else None,
            "max_drawdown_rs": dd, "days": len(res.dates), "days_with_trades": int(np.sum(daily != 0)),
            "disposition_counts": dict(res.disposition_counts)}


class WickPulledStore:
    """Sensitivity (Codex 26 Sep; Upstox ranges are wider than Kite's on 36% of bars): every 15-minute high is
    pulled DOWN and every low pulled UP by `ticks` ticks, never past the bar's open/close. Closes (and so every
    Z, z* and signal) are unchanged; only fills that depend on a high/low touch can change."""

    def __init__(self, store: Any, ticks: int) -> None:
        self._s, self.ticks = store, int(ticks)
        self.mode = getattr(store, "mode", "STRATEGY")

    def __getattr__(self, k: str) -> Any:
        return getattr(self._s, k)

    @property
    def symbols(self) -> List[str]:
        return self._s.symbols

    def _pull(self, b: Any) -> Any:
        import dataclasses

        from research.backtest.bars import tick_size

        body_hi, body_lo = max(b.open, b.close), min(b.open, b.close)
        hi = max(body_hi, round(b.high - self.ticks * tick_size(b.high), 2))
        lo = min(body_lo, round(b.low + self.ticks * tick_size(b.low), 2))
        return dataclasses.replace(b, high=hi, low=lo)

    def bars(self, symbol: str, day: date):
        return [self._pull(b) for b in self._s.bars(symbol, day)]

    def session_arrays(self, symbol: str, day: date):
        arr = self._s.session_arrays(symbol, day) if hasattr(self._s, "session_arrays") else None
        if arr is None:
            return None
        bs = self.bars(symbol, day)
        return {**arr, "high": np.array([b.high for b in bs]), "low": np.array([b.low for b in bs])}


def run_orbprod(limit_sessions: Optional[int] = None) -> Dict[str, Any]:
    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.strategies.orb_prod import OrbProdAdapter

    t0 = _time.perf_counter()
    spec, spec_path, store, universe, sector_map, (start, end), special, bad = _setup(limit_sessions)
    adapter = OrbProdAdapter()
    cfg = EngineConfig(var_elm_rate=0.20, stop_limit_offset_pct=0.005, r_basis="stop_limit")
    res = BacktestEngine(store, universe, [adapter], cfg).run()
    return {"strategy": "ORB_PROD", "design_window": [start.isoformat(), end.isoformat()],
            "sessions_run": len(res.dates), **_cf_summary(res.signals),
            "decision_reasons": dict(adapter.reasons),
            "r_basis": "stop_limit", "evidence_class": "E1_CF (never admissible)",
            "universe_flags": list(universe.flags), "data_source": "UPSTOX_API_V2",
            "production_manifest": adapter.manifest(), "runtime_s": round(_time.perf_counter() - t0, 1),
            "label": "DESIGN_SET_BASELINE"}


EVENT_HOLDS: Tuple[Any, ...] = (1, 2, 4, 8, "EOD")
HOLD_CHOICES: Tuple[Any, ...] = (4, 8, "EOD")
LAST_HELD_SLOT = 22          # plan A.9: bar 22 (14:30-14:45) is the last bar fully held


def event_rows(store: Any, candidates: Mapping[Tuple[str, date], Mapping[str, Any]],
               holds: Sequence[Any] = EVENT_HOLDS) -> List[Dict[str, Any]]:
    """Residual reversion after each candidate, h_eff = min(h, 22 - t) bars (EOD: 22 - t), from the close
    of the signal bar t to the close of bar t + h_eff:
      reversion_bps   = -sign(E) * [ln(C[j]/C[t]) - beta * ln(F[j]/F[t])] * 1e4   (residual move back)
      retrace_frac    = that residual reversion / |E[t]|                        (share of the move undone)
      trade_gross_bps = sg * ln(C[j] / O[t+1]) * 1e4        (gross, from the next bar's open; no costs)
    F is the factor the adapter actually used for that stock-day."""
    from research.features.session_cache import slot_series

    out: List[Dict[str, Any]] = []
    for (sym, day), cd in sorted(candidates.items()):
        bars = store.bars(sym, day)
        cl, op = slot_series(bars), slot_series(bars, "open")
        fcl = slot_series(store.bars(cd["factor_used"], day))
        t, beta, E, sg = int(cd["slot"]), float(cd["beta"]), float(cd["E"]), int(cd["sg"])
        for h in holds:
            he = (LAST_HELD_SLOT - t) if h == "EOD" else min(int(h), LAST_HELD_SLOT - t)
            if he <= 0:
                continue
            j = t + he
            vals = (cl[t], cl[j], fcl[t], fcl[j], op[t + 1])
            if not all(np.isfinite(v) and v > 0 for v in vals) or E == 0:
                continue
            resid = math.log(cl[j] / cl[t]) - beta * math.log(fcl[j] / fcl[t])
            rev = -math.copysign(1.0, E) * resid
            out.append({"symbol": sym, "day": day.isoformat(), "h": str(h), "h_eff": he, "slot": t,
                        "reversion_bps": 1e4 * rev, "retrace_frac": rev / abs(E),
                        "trade_gross_bps": 1e4 * sg * math.log(cl[j] / op[t + 1])})
    return out


def event_summary(rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    from research.decision.stats import clustered_se

    out: Dict[str, Any] = {}
    for h in EVENT_HOLDS:
        rs = [r for r in rows if r["h"] == str(h)]
        if not rs:
            continue
        days = [r["day"] for r in rs]
        stats: Dict[str, Any] = {"n": len(rs), "days": len(set(days)),
                                 "mean_h_eff": float(np.mean([r["h_eff"] for r in rs]))}
        for k in ("reversion_bps", "retrace_frac", "trade_gross_bps"):
            v = np.array([r[k] for r in rs], dtype=float)
            se = clustered_se(list(v), days) if v.size > 1 else math.nan
            stats[k] = {"mean": float(v.mean()), "median": float(np.median(v)),
                        "se_cluster_by_day": se if math.isfinite(se) else None,
                        "t_cluster": float(v.mean() / se) if math.isfinite(se) and se > 0 else None}
        out[str(h)] = stats
    return out


def _zstar_on_same_data(zstar_result: Path) -> float:
    """z* from a zstar run, accepted only if that run read the sealed snapshot this run reads."""
    from research.data import snapshot

    zdir = Path(zstar_result).parent
    res = json.loads(Path(zstar_result).read_text(encoding="utf-8"))
    man = json.loads((zdir / "manifest.json").read_text(encoding="utf-8"))
    here = snapshot.describe()
    theirs = man.get("history_inputs") or {}
    if here.get("kind") != "SEALED_SNAPSHOT" or theirs.get("content_sha256") != here.get("content_sha256"):
        raise SystemExit(f"REFUSED: {zstar_result} was not computed on this sealed snapshot "
                         f"({theirs.get('content_sha256')} vs {here.get('content_sha256')})")
    if man.get("limit_sessions"):
        raise SystemExit(f"REFUSED: {zstar_result} is a limited trial run (limit_sessions={man['limit_sessions']})")
    return float(res["z_star"])


def run_hold(hold: Any, variant: str, z_star: float, limit_sessions: Optional[int] = None, *,
             slippage_ticks: int = 1, r_basis: Optional[str] = None, wick_ticks: int = 0) -> Dict[str, Any]:
    import copy

    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.features.calibration import CalibrationConfig, CalibrationProvider
    from research.features.events import NoEventsData
    from research.strategies.resid_rev import ResidRevAdapter

    t0 = _time.perf_counter()
    spec, spec_path, store, universe, sector_map, (start, end), special, bad = _setup(limit_sessions)
    if wick_ticks:
        store = WickPulledStore(store, wick_ticks)
    spec_h = copy.deepcopy(spec)                   # in memory only: the YAML stays DRAFT until P7.3
    spec_h["signal"]["z_star"] = float(z_star)
    spec_h["trade"]["hold_bars"] = hold
    if variant == "RESID_REV":
        from research.data.nse_events import load_table_events

        events = load_table_events()
    else:
        events = NoEventsData()
    adapter = ResidRevAdapter(spec_h, variant=variant, mode="EMIT",
                              trading_days=store.sessions("IDX:NIFTY50"))
    universe_symbols = sorted({k[0] for k in universe.table})
    provider = CalibrationProvider(store, sector_map, CalibrationConfig.from_prereg(spec_h), symbols=universe_symbols)
    eng_cfg = spec_h["engine"]
    basis = r_basis or str(eng_cfg["r_basis"])
    cfg = EngineConfig(var_elm_rate=float(eng_cfg["var_elm_rate"]), allow_shorts=bool(eng_cfg["allow_shorts"]),
                       r_basis=basis, stop_limit_offset_pct=0.005, entry_slippage_ticks=slippage_ticks,
                       stop_slippage_ticks=slippage_ticks, exit_slippage_ticks=slippage_ticks)
    res = BacktestEngine(store, universe, [adapter], cfg, calibration_provider=provider,
                         events_provider=events).run()
    ev_rows = event_rows(store, adapter.candidates)
    return {"strategy": variant, "hold_bars": hold, "z_star": float(z_star),
            "design_window": [start.isoformat(), end.isoformat()], "sessions_run": len(res.dates),
            **_cf_summary(res.signals), "decision_reasons": dict(adapter.reasons),
            "candidates": len(adapter.candidates), "event_study": event_summary(ev_rows), "event_table": ev_rows,
            "events_coverage": getattr(events, "coverage", "NONE"),
            "portfolio": _portfolio_summary(res), "slippage_ticks": slippage_ticks, "wick_pull_ticks": wick_ticks,
            "r_basis": basis, "evidence_class": "E1_CF (design set; selection of h)",
            "universe_flags": list(universe.flags), "data_source": "UPSTOX_API_V2",
            "runtime_s": round(_time.perf_counter() - t0, 1), "label": "DESIGN_SET_HOLD_CHOICE"}


def run_dir(args: Any, started: datetime) -> Path:
    """A new folder per run: task, options, start second and process id. Never reused (exist_ok=False)."""
    import os

    parts = [args.task]
    if args.task == "hold":
        parts.append(f"h{args.hold}")
        if getattr(args, "variant", "RESID_REV_NF") != "RESID_REV_NF":
            parts.append(args.variant)
        if getattr(args, "slippage_ticks", 1) != 1:
            parts.append(f"slip{args.slippage_ticks}")
        if getattr(args, "r_basis", None):
            parts.append(args.r_basis)
        if getattr(args, "wick_pull_ticks", 0):
            parts.append(f"wick{args.wick_pull_ticks}")
    d = paths.outputs_dir() / "p7" / f"{'_'.join(parts)}_{started:%Y%m%d_%H%M%S}_{os.getpid()}"
    d.mkdir(parents=True, exist_ok=False)
    return d


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="P7.2 design-set tasks")
    ap.add_argument("task", choices=["zstar", "orbprod", "hold"])
    ap.add_argument("--hold", choices=["4", "8", "EOD"], default=None, help="hold task: h")
    ap.add_argument("--variant", choices=["RESID_REV", "RESID_REV_NF"], default="RESID_REV_NF")
    ap.add_argument("--zstar-result", default=None, help="hold task: result.json of a zstar run on this snapshot")
    ap.add_argument("--slippage-ticks", type=int, default=1, help="hold task: ticks per side (sensitivity: 2)")
    ap.add_argument("--r-basis", choices=["stop_limit", "trigger"], default=None)
    ap.add_argument("--wick-pull-ticks", type=int, default=0, help="hold task: pull highs/lows in (sensitivity: 2)")
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
    # the code a run imports is the code at its start: record it now, not when the manifest is written
    code_at_start = {"git_commit_at_start": _git_commit(), "git_dirty_research_at_start": _git_dirty_research()}
    started = datetime.now(IST)
    out_dir = run_dir(args, started)
    from research.studies import prereg_io

    spec_path = prereg_io.PREREG_DIR / "resid_rev_v1.yaml"
    if args.task == "zstar":
        res, scan = run_zstar(args.limit_sessions)
        import pandas as pd

        pd.DataFrame([{"symbol": s, "session": d.isoformat(), "max_abs_z": v} for (s, d), v in scan.items()]) \
            .to_parquet(out_dir / "scan_max.parquet", index=False)
    elif args.task == "orbprod":
        import pandas as pd

        res = run_orbprod(args.limit_sessions)
        pd.DataFrame(res.pop("signals_table")).to_parquet(out_dir / "signals.parquet", index=False)
    else:
        import pandas as pd

        if args.hold is None or args.zstar_result is None:
            print("REFUSED: the hold task needs --hold and --zstar-result")
            return 2
        z = _zstar_on_same_data(Path(args.zstar_result))
        res = run_hold(args.hold if args.hold == "EOD" else int(args.hold), args.variant, z, args.limit_sessions,
                       slippage_ticks=args.slippage_ticks, r_basis=args.r_basis, wick_ticks=args.wick_pull_ticks)
        res["zstar_result"] = str(args.zstar_result)
        pd.DataFrame(res.pop("signals_table")).to_parquet(out_dir / "signals.parquet", index=False)
        pd.DataFrame(res.pop("event_table")).to_parquet(out_dir / "event_study.parquet", index=False)
    (out_dir / "result.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    (out_dir / "manifest.json").write_text(json.dumps(manifest(args.task, spec_path,
                                                               {"limit_sessions": args.limit_sessions,
                                                                "seed": 20260925,
                                                                "history_inputs": inputs,
                                                                "history_inputs_after_run": snapshot.describe(),
                                                                "universe_table_sha256_at_start":
                                                                    universe_sha_start, **code_at_start},
                                                      started),
                                                      indent=1, default=str), encoding="utf-8")
    brief = {k: v for k, v in res.items() if k not in ("daily_cf_r", "production_manifest")}
    print(json.dumps(brief, indent=1, default=str))
    print(f"written: {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
