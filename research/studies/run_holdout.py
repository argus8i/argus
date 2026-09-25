"""
research/studies/run_holdout.py
===============================
P7.4: the ONE holdout run of RESID_REV v1 (plan P7.4; Yashu's overnight mandate of 26 Sep 2026).

    python -m research.studies.run_holdout lock                 # after the LOCKED YAML is committed
    python -m research.studies.run_holdout run --part primary   # and the other PARTS, any order
    python -m research.studies.run_holdout finalize             # decision, register, done marker

lock      Writes resid_rev_v1.lock = {id, yaml_sha256, commit, locked_at}. Refused unless the YAML says
          status LOCKED, has z_star and hold_bars, and is committed unchanged at HEAD (the lock cites HEAD).
run       One part of the study. Refused unless:
            - the lock verifies (prereg_io.verify_lock) and resid_rev_v1.holdout_done does not exist;
            - the history is a sealed snapshot, sealed AFTER the lock (its universe table was built with the
              holdout visible), whose data files are byte-identical to the design snapshot named in the YAML
              (only reference/ tables may differ): the holdout reads exactly the data the design read;
            - the code is committed (no uncommitted research/ changes).
          z_star, hold_bars and the variant come from the locked YAML only; nothing is overridden.
          Calibration reads the sessions before the holdout (a store window from the design start), and the
          engine evaluates only holdout sessions.
finalize  Needs every part, all from the same lock, code commit and snapshot. Applies the pre-registered
          primary test (day-clustered t >= 2.0 and mean net R > 0), writes the E1 study record, moves the
          register through promotion.apply_holdout (SHADOW or REJECTED), and writes the done marker. After
          that no part can run again.
Every part writes <outputs>/p7/holdout/<part>/ (result.json, signals.parquet) and appends its intents to the
append-only ledger <outputs>/p7/holdout/holdout_ledger.db (mode BACKTEST).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import numpy as np

from research.data import paths, snapshot
from research.studies import prereg_io

IST = timezone(timedelta(hours=5, minutes=30))
SPEC = prereg_io.PREREG_DIR / "resid_rev_v1.yaml"
LOCK = SPEC.with_suffix(".lock")
STARTED = SPEC.with_suffix(".holdout_started")
DONE = SPEC.with_suffix(".holdout_done")
PARTS = {
    "primary": {"slippage_ticks": 1, "r_basis": "stop_limit", "wick_ticks": 0},
    "slippage_2_ticks": {"slippage_ticks": 2, "r_basis": "stop_limit", "wick_ticks": 0},
    "r_basis_trigger": {"slippage_ticks": 1, "r_basis": "trigger", "wick_ticks": 0},
    "wick_pulled_in_2_ticks": {"slippage_ticks": 1, "r_basis": "stop_limit", "wick_ticks": 2},
    "orb_prod": {},
}
DATA_PREFIXES = ("bars_15m/", "daily/", "events/", "bhavcopy/", "raw/", "manifests/")


class HoldoutRefused(RuntimeError):
    pass


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=paths.repo_root(), capture_output=True, text=True,
                          timeout=30).stdout.strip()


def _out_root() -> Path:
    return paths.ensure(paths.outputs_dir() / "p7" / "holdout")


# ---------------------------------------------------------------------------------------------- lock
def write_lock(spec_path: Path = SPEC, now: Optional[datetime] = None) -> Dict[str, Any]:
    spec = prereg_io.load(spec_path)
    if spec.get("status") != "LOCKED":
        raise HoldoutRefused(f"{spec_path.name}: status is {spec.get('status')}, not LOCKED")
    if spec["signal"].get("z_star") is None or spec["trade"].get("hold_bars") is None:
        raise HoldoutRefused("z_star and hold_bars must be set before locking")
    rel = spec_path.relative_to(paths.repo_root()).as_posix()
    if _git("status", "--porcelain", "--", rel):
        raise HoldoutRefused(f"{rel} has uncommitted changes; commit the LOCKED YAML first")
    head = _git("rev-parse", "HEAD")
    committed = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=paths.repo_root(), capture_output=True,
                               timeout=30).stdout.replace(b"\r\n", b"\n")
    sha = prereg_io.normalised_sha256(spec_path)
    if hashlib.sha256(committed).hexdigest() != sha:
        raise HoldoutRefused(f"{rel} at HEAD differs from the working file")
    lock = {"id": spec["id"], "yaml_sha256": sha, "commit": head,
            "locked_at": (now or datetime.now(IST)).isoformat(timespec="seconds")}
    spec_path.with_suffix(".lock").write_text(json.dumps(lock, indent=1) + "\n", encoding="utf-8")
    return lock


# ---------------------------------------------------------------------------------------------- checks
def _snapshot_files(path: Path) -> Dict[str, str]:
    info = json.loads((path / snapshot.SEAL).read_text(encoding="utf-8"))
    return {k: v["sha256"] for k, v in info["files"].items() if k.startswith(DATA_PREFIXES)}


def preflight(spec_path: Path = SPEC) -> Dict[str, Any]:
    status = prereg_io.verify_lock(spec_path)
    if not status.valid:
        raise HoldoutRefused(f"lock does not verify: {status.reason}")
    if DONE.exists():
        raise HoldoutRefused(f"{DONE.name} exists: the holdout has been run and finalized")
    spec = prereg_io.load(spec_path)
    here = snapshot.describe()
    if here["kind"] != "SEALED_SNAPSHOT":
        raise HoldoutRefused("the history is not a sealed snapshot")
    if here["sealed_at"] <= status.lock["locked_at"]:
        raise HoldoutRefused(f"snapshot sealed {here['sealed_at']}, before the lock {status.lock['locked_at']}: "
                             "its universe was built with the holdout hidden")
    design = spec["data"]["snapshot"]
    dpath = paths.history_dir().parent / design["name"]
    dinfo = snapshot.verify(dpath)
    if dinfo["content_sha256"] != design["content_sha256"]:
        raise HoldoutRefused(f"design snapshot {design['name']} does not match the YAML")
    mine, theirs = _snapshot_files(paths.history_dir()), _snapshot_files(dpath)
    if mine != theirs:
        diff = sorted(set(mine.items()) ^ set(theirs.items()))[:5]
        raise HoldoutRefused(f"holdout data files differ from the design snapshot: {diff}")
    dirty = _git("status", "--porcelain", "--", "research")
    if dirty:
        raise HoldoutRefused(f"uncommitted research/ changes: {dirty.splitlines()[:3]}")
    return {"lock": status.lock, "snapshot": here, "design_snapshot": dinfo, "code_commit": _git("rev-parse", "HEAD")}


def _started(pre: Mapping[str, Any]) -> None:
    rec = {"lock": pre["lock"], "code_commit": pre["code_commit"], "snapshot": pre["snapshot"]["content_sha256"]}
    if STARTED.exists():
        old = json.loads(STARTED.read_text(encoding="utf-8"))
        if {k: old.get(k) for k in rec} != rec:
            raise HoldoutRefused(f"{STARTED.name} records a different lock/commit/snapshot: {old}")
        return
    STARTED.write_text(json.dumps({**rec, "started_at": datetime.now(IST).isoformat(timespec="seconds")},
                                  indent=1) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------------------------- runs
def _setup(spec: Mapping[str, Any]):
    import pandas as pd

    from research.data.sector_map import load_sector_map
    from research.data.store_parquet import ParquetCandleStore
    from research.data.universe_build import TableUniverse
    from research.studies.p7_design import DesignWindowStore, quality_exclusions

    d0 = date.fromisoformat(str(spec["data"]["design"][0]))
    h0, h1 = (date.fromisoformat(str(x)) for x in spec["data"]["holdout"])
    base = ParquetCandleStore()
    if base.guard.hidden(h0):
        raise HoldoutRefused("the HoldoutGuard still hides the holdout (lock not valid)")
    validity = pd.read_parquet(paths.reference_dir() / "session_validity.parquet",
                               columns=["symbol", "session", "valid"]).to_dict("records")
    special, bad = quality_exclusions(validity, d0, h1)       # the design-set rule, applied unchanged
    store = DesignWindowStore(base, d0, h1, special, bad)       # calibration may read pre-holdout sessions
    days = sorted({d for s in base.symbols if base.kind(s) == "TRADABLE" for d in store.sessions(s) if h0 <= d <= h1})
    universe = TableUniverse.from_parquet()
    n_elig = sum(1 for (s, d), (e, _) in universe.table.items() if e and h0 <= d <= h1)
    if n_elig == 0:
        raise HoldoutRefused("the universe table has no eligible holdout stock-days")
    return store, universe, load_sector_map(), days, (h0, h1), special, bad, n_elig


def run_part(part: str, spec_path: Path = SPEC) -> Dict[str, Any]:
    import time as _time

    from research.backtest.engine import BacktestEngine, EngineConfig
    from research.decision.ledger import Ledger, rows_from_engine
    from research.features.calibration import CalibrationConfig, CalibrationProvider
    from research.features.events import NoEventsData
    from research.studies.p7_design import WickPulledStore, _cf_summary, _portfolio_summary

    if part not in PARTS:
        raise HoldoutRefused(f"unknown part {part!r}")
    pre = preflight(spec_path)
    _started(pre)
    spec = prereg_io.load(spec_path)
    t0 = _time.perf_counter()
    store, universe, sector_map, days, (h0, h1), special, bad, n_elig = _setup(spec)
    opts = PARTS[part]
    if part == "orb_prod":
        from research.strategies.orb_prod import OrbProdAdapter

        adapter = OrbProdAdapter()
        cfg = EngineConfig(var_elm_rate=0.20, stop_limit_offset_pct=0.005, r_basis="stop_limit")
        res = BacktestEngine(store, universe, [adapter], cfg).run(only_dates=days)
        strategy, variant = "ORB_PROD", "ORB_PROD"
    else:
        from research.strategies.resid_rev import ResidRevAdapter

        variant = str(spec["holdout_variant"])
        if opts["wick_ticks"]:
            store = WickPulledStore(store, opts["wick_ticks"])
        adapter = ResidRevAdapter(spec, variant=variant, mode="EMIT", trading_days=store.sessions("IDX:NIFTY50"))
        provider = CalibrationProvider(store, sector_map, CalibrationConfig.from_prereg(spec),
                                       symbols=sorted({k[0] for k in universe.table}))
        eng = spec["engine"]
        cfg = EngineConfig(var_elm_rate=float(eng["var_elm_rate"]), allow_shorts=bool(eng["allow_shorts"]),
                           r_basis=opts["r_basis"], stop_limit_offset_pct=0.005,
                           entry_slippage_ticks=opts["slippage_ticks"], stop_slippage_ticks=opts["slippage_ticks"],
                           exit_slippage_ticks=opts["slippage_ticks"])
        events = NoEventsData()
        if variant == "RESID_REV":
            from research.data.nse_events import load_table_events

            events = load_table_events()
        res = BacktestEngine(store, universe, [adapter], cfg, calibration_provider=provider,
                             events_provider=events).run(only_dates=days)
        strategy = "RESID_REV"
    summ = _cf_summary(res.signals)
    table = summ.pop("signals_table")
    sides = {}
    for side in ("BUY", "SELL"):
        sub = [r for r in table if r["side"] == side]
        sides["long_only" if side == "BUY" else "short_only"] = _subset_stats(sub)
    out_dir = paths.ensure(_out_root() / part)
    import pandas as pd

    pd.DataFrame(table).to_parquet(out_dir / "signals.parquet", index=False)
    result = {"part": part, "strategy": strategy, "variant": variant, "window": [h0.isoformat(), h1.isoformat()],
              "sessions_run": len(res.dates), "eligible_stock_days": n_elig,
              "special_sessions_excluded": sorted(d.isoformat() for d in special if h0 <= d <= h1),
              "invalid_symbol_sessions_excluded": sum(1 for _, d in bad if h0 <= d <= h1), **summ,
              "by_side": sides, "portfolio": _portfolio_summary(res), "options": opts,
              "decision_reasons": dict(getattr(adapter, "reasons", {})), "universe_flags": list(universe.flags),
              "lock": pre["lock"], "code_commit": pre["code_commit"],
              "snapshot": pre["snapshot"]["content_sha256"], "runtime_s": round(_time.perf_counter() - t0, 1)}
    (out_dir / "result.json").write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")
    rows = rows_from_engine(res, run_id=f"holdout_{spec['id']}_{part}", mode="BACKTEST",
                            strategy_version={strategy: f"{spec['id']}:{pre['lock']['yaml_sha256'][:16]}",
                                              variant: f"{spec['id']}:{pre['lock']['yaml_sha256'][:16]}"},
                            prereg_sha=pre["lock"]["yaml_sha256"], data_hash=pre["snapshot"]["content_sha256"],
                            code_commit=pre["code_commit"])
    if rows:
        Ledger(_out_root() / "holdout_ledger.db").append(rows)
    return result


def _subset_stats(rows: List[Mapping[str, Any]]) -> Dict[str, Any]:
    from research.decision.stats import clustered_se

    net = np.array([r["net_r"] for r in rows], dtype=float)
    if net.size < 2:
        return {"n": int(net.size), "mean_net_r": float(net.mean()) if net.size else None}
    days = [str(r["signal_time"])[:10] for r in rows]
    se = clustered_se(list(net), days)
    return {"n": int(net.size), "mean_net_r": float(net.mean()), "se_cluster_by_day": se,
            "t_cluster": float(net.mean() / se) if math.isfinite(se) and se > 0 else None}


# ---------------------------------------------------------------------------------------------- finalize
def primary_test(result: Mapping[str, Any], spec: Mapping[str, Any]) -> Tuple[bool, List[str]]:
    thr = float(spec["primary_test"]["threshold"])
    t, mean = result.get("t_cluster"), result.get("mean_net_r")
    ok = t is not None and mean is not None and math.isfinite(t) and math.isfinite(mean) and t >= thr and mean > 0
    return bool(ok), [f"primary: day-clustered t {t} (threshold {thr}), mean net R {mean} (must be > 0), "
                      f"n {result.get('signals')} on {result.get('signal_days')} days -> {'PASS' if ok else 'FAIL'}"]


def finalize(spec_path: Path = SPEC, register_path: Optional[Path] = None) -> Dict[str, Any]:
    from research.decision import promotion

    pre = preflight(spec_path)
    spec = prereg_io.load(spec_path)
    results = {}
    for part in PARTS:
        f = _out_root() / part / "result.json"
        if not f.exists():
            raise HoldoutRefused(f"part {part} has not run")
        r = json.loads(f.read_text(encoding="utf-8"))
        if (r["lock"] != pre["lock"] or r["code_commit"] != pre["code_commit"]
                or r["snapshot"] != pre["snapshot"]["content_sha256"]):
            raise HoldoutRefused(f"part {part} ran under a different lock, commit or snapshot")
        results[part] = r
    passed, reasons = primary_test(results["primary"], spec)
    secondary = {p: {k: results[p].get(k) for k in ("signals", "mean_net_r", "t_cluster", "mean_gross_r",
                                                      "mean_fee_r", "mean_slip_r")}
                 for p in ("slippage_2_ticks", "r_basis_trigger", "wick_pulled_in_2_ticks", "orb_prod")}
    secondary["long_only"] = results["primary"]["by_side"]["long_only"]
    secondary["short_only"] = results["primary"]["by_side"]["short_only"]
    record = {"strategy_id": "RESID_REV", "variant": results["primary"]["variant"], "evidence_class": "E1",
              "prereg_sha256": pre["lock"]["yaml_sha256"], "passed": passed, "reasons": reasons,
              "primary": {k: results["primary"].get(k) for k in ("signals", "signal_days", "mean_net_r",
                                                                  "se_cluster_by_day", "t_cluster", "mean_gross_r",
                                                                  "mean_fee_r", "mean_slip_r", "portfolio")},
              "secondary": secondary, "window": results["primary"]["window"], "code_commit": pre["code_commit"],
              "snapshot": pre["snapshot"]["content_sha256"],
              "finalized_at": datetime.now(IST).isoformat(timespec="seconds")}
    (_out_root() / "holdout_record.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    kw = {"register_path": register_path} if register_path else {}
    decision = promotion.apply_holdout("RESID_REV", record, spec_path, **kw)
    DONE.write_text(json.dumps({"finalized_at": record["finalized_at"], "passed": passed, "lock": pre["lock"],
                                "code_commit": pre["code_commit"], "snapshot": record["snapshot"],
                                "record_sha256": hashlib.sha256(json.dumps(record, sort_keys=True, default=str)
                                                                .encode()).hexdigest(),
                                "register_transition": decision["to"]}, indent=1) + "\n", encoding="utf-8")
    return {"record": record, "decision": decision}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="P7.4 holdout run (exactly once)")
    ap.add_argument("cmd", choices=["lock", "run", "finalize", "preflight"])
    ap.add_argument("--part", choices=sorted(PARTS), default=None)
    args = ap.parse_args(argv)
    try:
        if args.cmd == "lock":
            print(json.dumps(write_lock(), indent=1))
        elif args.cmd == "preflight":
            print(json.dumps(preflight(), indent=1, default=str))
        elif args.cmd == "run":
            if not args.part:
                raise HoldoutRefused("--part is required")
            r = run_part(args.part)
            print(json.dumps({k: v for k, v in r.items() if k not in ("daily_cf_r",)}, indent=1, default=str))
        else:
            out = finalize()
            print(json.dumps({"passed": out["record"]["passed"], "reasons": out["record"]["reasons"],
                              "register": out["decision"]["to"]}, indent=1))
    except HoldoutRefused as exc:
        print(f"REFUSED: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
