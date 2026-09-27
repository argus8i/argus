"""
research/studies/event_holdout.py
=================================
The ONE holdout run of an event-study pre-registration: BAN_ENTRY_SHORT_v1 and EXPIRY_RELIEF_LONG_v1
(Yashu's approval, 26 Sep 2026 12:39 IST). Same discipline as run_holdout.py (RESID_REV), generalised.

    python -m research.studies.event_holdout lock      --study ban_entry_short_v1
    python -m research.studies.event_holdout preflight --study ban_entry_short_v1
    python -m research.studies.event_holdout run       --study ban_entry_short_v1   # every part
    python -m research.studies.event_holdout finalize  --study ban_entry_short_v1

lock      <study>.lock = {id, yaml_sha256, commit, locked_at}; refused unless the YAML says LOCKED and is committed
          unchanged at HEAD, and its pass_rule is the one this runner applies.
run       refused unless: the lock verifies; <study>.holdout_done does not exist; the research code is committed
          (identity = blob hashes of research/**/*.py); the holdout snapshot the YAML pins verifies with the pinned
          content hash; the design snapshot verifies with its pinned hash; and the two agree on every data file
          (bars_15m/, daily/, events/, bhavcopy/, raw/, manifests/), so the holdout reads the design's bytes and
          only the universe table (reference/) differs. The first run writes <study>.holdout_started; a later run
          under a different lock, code or snapshot refuses. Each part writes
          <outputs>/holdout/<study>/<part>/result.json (+ signals.parquet).
finalize  applies the YAML's pass_rule to the parts, writes the E1 study record, registers the strategy as
          UNVERIFIED if it is new, moves it through promotion.apply_holdout (SHADOW or REJECTED), and writes
          <study>.holdout_done. After that nothing runs again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pandas as pd

from research.data import paths, snapshot
from research.studies import prereg_io
from research.studies.run_holdout import DATA_PREFIXES, HoldoutRefused, code_state

IST = timezone(timedelta(hours=5, minutes=30))

STUDIES: Dict[str, Dict[str, Any]] = {
    "ban_entry_short_v1": {
        "strategy_id": "BAN_ENTRY_SHORT",
        "pass_rule": "primary_test AND slippage_2_ticks mean_net_r > 0",
        "parts": {
            "primary": {"entry_model": "vwap_0915_0919", "stop": 0.03, "slippage_ticks": 1},
            "slippage_2_ticks": {"entry_model": "vwap_0915_0919", "stop": 0.03, "slippage_ticks": 2},
            "stop_2pct": {"entry_model": "vwap_0915_0919", "stop": 0.02, "slippage_ticks": 1},
            "entry_auction_open": {"entry_model": "auction_open", "stop": 0.03, "slippage_ticks": 1},
            "entry_first_minute_low": {"entry_model": "first_minute_low", "stop": 0.03, "slippage_ticks": 1},
        },
        "notes": "F&O-ban first-session MIS short (26 Sep edge research); design +0.118R t 3.86",
    },
    "expiry_relief_long_v1": {
        "strategy_id": "EXPIRY_RELIEF_LONG",
        "pass_rule": "primary_test AND second_gate AND slippage_2_ticks mean_net_r > 0",
        "parts": {
            "primary": {},
            "slippage_2_ticks": {"slippage_ticks": 2},
            "capacity_3_most_oversold": {"capacity": 3},
            "stop_5pct_no_target": {"stop_pct": 0.05, "target_r": None},
        },
        "notes": "post-expiry rebound of 20-session losers, CNC 5 sessions (Antigravity idea); design +0.494R t 3.89",
    },
}


def _study(study: str) -> Dict[str, Any]:
    if study not in STUDIES:
        raise HoldoutRefused(f"unknown study {study!r}; known: {sorted(STUDIES)}")
    return STUDIES[study]


def spec_mismatches(study: str, spec: Mapping[str, Any]) -> List[str]:
    """Every parameter the runner applies, checked against the locked YAML (Codex, 26 Sep): the pass rule, the
    primary's entry, stop and slippage, and the set of secondary parts. Empty when they agree."""
    cfg, out = _study(study), []
    if str(spec.get("pass_rule")) != cfg["pass_rule"]:
        out.append(f"pass_rule {spec.get('pass_rule')!r} != runner {cfg['pass_rule']!r}")
    names = {str(s["name"]) for s in spec.get("secondary_tests") or []}
    missing = sorted(set(cfg["parts"]) - {"primary"} - names)
    if missing:
        out.append(f"runner parts not declared in secondary_tests: {missing}")
    def get(*keys: str) -> Any:
        node: Any = spec
        for k in keys:
            node = node.get(k) if isinstance(node, Mapping) else None
        return node

    def num(*keys: str) -> float:
        try:
            return float(get(*keys))
        except (TypeError, ValueError):
            return math.nan                              # a missing or malformed field never matches

    checks: List[Tuple[str, bool]] = [("costs.slippage_ticks_per_side == 1", num("costs", "slippage_ticks_per_side") == 1)]
    if study == "ban_entry_short_v1":
        p = cfg["parts"]["primary"]
        checks += [(f"trade.entry.rule == {p['entry_model']}", str(get("trade", "entry", "rule")) == p["entry_model"]),
                   (f"trade.stop.sl_trigger_pct_above_entry_ref == {p['stop'] * 100:g}",
                    abs(num("trade", "stop", "sl_trigger_pct_above_entry_ref") / 100 - p["stop"]) < 1e-12),
                   ("trade.stop.sl_limit_offset_pct == 0.5", abs(num("trade", "stop", "sl_limit_offset_pct") - 0.5) < 1e-12),
                   ("trade.capacity.participation_of_entry_window_volume == 0.10",
                    abs(num("trade", "capacity", "participation_of_entry_window_volume") - 0.10) < 1e-12)]
    else:
        from research.studies import expiry_relief as er

        checks += [("trade.stop.pct_below_entry == 3.0", abs(num("trade", "stop", "pct_below_entry") - 3.0) < 1e-12),
                   ("trade.target == half at 1.5R",
                    num("trade", "target", "r_multiple") == 1.5 and num("trade", "target", "fraction") == 0.5),
                   ("expiry_relief constants == (-5.0, 20, 5)", (er.DROP_PCT, er.LOOKBACK, er.HOLD) == (-5.0, 20, 5))]
    out += [f"not {name}" for name, ok in checks if not ok]
    return out


def spec_path(study: str) -> Path:
    return prereg_io.PREREG_DIR / f"{study}.yaml"


def _marker(study: str, kind: str) -> Path:
    return spec_path(study).with_suffix(f".{kind}")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=paths.repo_root(), capture_output=True, text=True,
                          timeout=30).stdout.strip()


def _out(study: str, part: Optional[str] = None) -> Path:
    p = paths.outputs_dir() / "holdout" / study
    return paths.ensure(p / part if part else p)


# ---------------------------------------------------------------------------------------------- lock
def write_lock(study: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    cfg, sp = _study(study), spec_path(study)
    spec = prereg_io.load(sp)
    if spec.get("status") != "LOCKED":
        raise HoldoutRefused(f"{sp.name}: status is {spec.get('status')}, not LOCKED")
    bad = spec_mismatches(study, spec)
    if bad:
        raise HoldoutRefused(f"{sp.name} does not match the runner: {bad}")
    rel = sp.relative_to(paths.repo_root()).as_posix()
    if _git("status", "--porcelain", "--", rel):
        raise HoldoutRefused(f"{rel} has uncommitted changes; commit the LOCKED YAML first")
    committed = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=paths.repo_root(), capture_output=True,
                               timeout=30).stdout.replace(b"\r\n", b"\n")
    sha = prereg_io.normalised_sha256(sp)
    if hashlib.sha256(committed).hexdigest() != sha:
        raise HoldoutRefused(f"{rel} at HEAD differs from the working file")
    lock = {"id": spec["id"], "yaml_sha256": sha, "commit": _git("rev-parse", "HEAD"),
            "locked_at": (now or datetime.now(IST)).isoformat(timespec="seconds")}
    _marker(study, "lock").write_text(json.dumps(lock, indent=1) + "\n", encoding="utf-8")
    return lock


# ---------------------------------------------------------------------------------------------- checks
def _data_files(path: Path) -> Dict[str, str]:
    info = json.loads((path / snapshot.SEAL).read_text(encoding="utf-8"))
    return {k: v["sha256"] for k, v in info["files"].items() if k.startswith(DATA_PREFIXES)}


def preflight(study: str, snapshots: Optional[Path] = None) -> Dict[str, Any]:
    _study(study)
    sp = spec_path(study)
    status = prereg_io.verify_lock(sp)
    if not status.valid:
        raise HoldoutRefused(f"lock does not verify: {status.reason}")
    if _marker(study, "holdout_done").exists():
        raise HoldoutRefused(f"{study}.holdout_done exists: the holdout has been run and finalized")
    spec = prereg_io.load(sp)
    bad = spec_mismatches(study, spec)
    if bad:
        raise HoldoutRefused(f"{sp.name} does not match the runner: {bad}")
    root = snapshots or snapshot.snapshots_root(paths.main_checkout() / "shared" / "track2_liquid" / "history")
    pins = {}
    for key in ("snapshot", "holdout_snapshot"):
        pin = spec["data"][key]
        info = snapshot.verify(root / pin["name"])
        if info["content_sha256"] != pin["content_sha256"]:
            raise HoldoutRefused(f"{key} {pin['name']} content {info['content_sha256'][:12]} is not the pinned "
                                 f"{pin['content_sha256'][:12]}")
        pins[key] = {"path": root / pin["name"], "content_sha256": info["content_sha256"],
                     "sealed_at": info.get("sealed_at")}
    a, b = _data_files(pins["holdout_snapshot"]["path"]), _data_files(pins["snapshot"]["path"])
    if a != b:
        diff = sorted(set(a.items()) ^ set(b.items()))[:5]
        raise HoldoutRefused(f"holdout snapshot data files differ from the design snapshot: {diff}")
    code = code_state()
    if code["dirty"]:
        raise HoldoutRefused(f"uncommitted research code: {code['dirty'][:3]}")
    return {"lock": status.lock, "spec": spec, "holdout_snapshot": pins["holdout_snapshot"],
            "design_snapshot": pins["snapshot"], "code_commit": code["head"], "code_identity": code["identity"]}


def _started(study: str, pre: Mapping[str, Any]) -> None:
    rec = {"lock": pre["lock"], "code_identity": pre["code_identity"],
           "snapshot": pre["holdout_snapshot"]["content_sha256"]}
    m = _marker(study, "holdout_started")
    if m.exists():
        old = json.loads(m.read_text(encoding="utf-8"))
        if {k: old.get(k) for k in rec} != rec:
            raise HoldoutRefused(f"{m.name} records a different lock/code/snapshot: {old}")
        return
    m.write_text(json.dumps({**rec, "started_at": datetime.now(IST).isoformat(timespec="seconds")}, indent=1) + "\n",
                 encoding="utf-8")


# ---------------------------------------------------------------------------------------------- parts
def _run_ban(pre: Mapping[str, Any], part: str, opts: Mapping[str, Any], out_dir: Path) -> Dict[str, Any]:
    from research.decision.stats import clustered_se
    from research.studies import ban_entry as be

    spec, snap = pre["spec"], pre["holdout_snapshot"]["path"]
    h0, h1 = (str(x) for x in spec["data"]["holdout"])
    raw = paths.main_checkout() / "shared" / "track2_liquid" / "history" / "raw" / "upstox"
    vm = be.VerifiedMinutes.sealed(snap, raw, expected_content_sha256=pre["holdout_snapshot"]["content_sha256"])
    counts: Dict[str, int] = {}
    ev = be.ban_entries(snap, h0, h1, counts=counts)
    pc = be.daily_prev_close(snap, sorted(set(ev.symbol)))
    rows = be.simulate_minutes(vm, ev, pc, opts["stop"], opts["entry_model"], slippage_ticks=opts["slippage_ticks"])
    df = pd.DataFrame(rows)
    df.to_parquet(out_dir / "signals.parquet", index=False)
    (out_dir / "verified_files.json").write_text(json.dumps(vm.verified, indent=0), encoding="utf-8")
    res: Dict[str, Any] = {"summary": be.summarise(rows), "event_counts": counts, "verified_files": len(vm.verified)}
    if part == "primary" and len(df) and "net_r" in df:
        u = pd.read_parquet(snap / "reference" / "universe_daily.parquet", columns=["symbol", "session", "dtv20_cr"])
        u["day"] = u.session.astype(str)
        f = df[df.disposition.isin(["FILLED", "PARTIAL"])].merge(u[["symbol", "day", "dtv20_cr"]],
                                                                   on=["symbol", "day"], how="left")
        top = f.sort_values(["day", "dtv20_cr"], ascending=[True, False]).groupby("day").head(3)
        v = top.net_r.to_numpy(float)
        se = clustered_se(list(v), list(top.day)) if len(v) > 1 else math.nan
        res["portfolio_3_slots"] = {"n": len(top), "days_over_3": int((f.groupby("day").size() > 3).sum()),
                                    "mean_net_r": float(v.mean()) if len(v) else None,
                                    "t_cluster": float(v.mean() / se) if len(v) > 1 and se > 0 else None}
    return res


def _run_expiry(pre: Mapping[str, Any], part: str, opts: Mapping[str, Any], out_dir: Path) -> Dict[str, Any]:
    from research.studies import expiry_relief as er
    from research.studies.strategy_lab import Panel

    spec, snap = pre["spec"], pre["holdout_snapshot"]["path"]
    h0, h1 = (str(x) for x in spec["data"]["holdout"])
    P = Panel(snap, end=h1, unlock_spec=spec_path("expiry_relief_long_v1"))
    r = er.evaluate(P, snap, h0, h1, **opts)
    sim = r.pop("_sim")
    sim.to_parquet(out_dir / "signals.parquet", index=False)
    if part == "primary":
        wd = pd.to_datetime(sim.expiry).dt.dayofweek
        r["expiry_weekday_change"] = {name: er.summarise(sim[mask]) for name, mask in
                                      (("thursday_expiries", wd == 3), ("other_weekday_expiries", wd != 3))
                                      if mask.any()}
        f = sim[sim.disposition == "FILLED"]
        per = f.groupby("expiry").net_r.agg(["size", "mean"])
        best3 = per["mean"].nlargest(3).index
        r["per_expiry"] = {"table": {e: [int(x["size"]), round(float(x["mean"]), 4)] for e, x in per.iterrows()},
                           "positive": int((per["mean"] > 0).sum()), "expiries": len(per),
                           "without_best_3": er.summarise(sim[~sim.expiry.isin(best3)])}
    return r


RUNNERS = {"ban_entry_short_v1": _run_ban, "expiry_relief_long_v1": _run_expiry}


def run_part(study: str, part: str, snapshots: Optional[Path] = None) -> Dict[str, Any]:
    import time as _time

    cfg = _study(study)
    if part not in cfg["parts"]:
        raise HoldoutRefused(f"unknown part {part!r} for {study}")
    pre = preflight(study, snapshots)
    _started(study, pre)
    t0 = _time.perf_counter()
    out_dir = _out(study, part)
    res = RUNNERS[study](pre, part, cfg["parts"][part], out_dir)
    seal = json.loads((pre["holdout_snapshot"]["path"] / snapshot.SEAL).read_text(encoding="utf-8"))["files"]
    used = ("daily/", "events/fo_ban", "reference/universe_daily", "reference/fno_point_in_time",
            "raw/upstox/manifest.jsonl")
    res.update({"study": study, "part": part, "options": cfg["parts"][part],
                "data_file_sha256": {k: v["sha256"] for k, v in seal.items() if k.startswith(used)},
                "window": [str(x) for x in pre["spec"]["data"]["holdout"]], "lock": pre["lock"],
                "code_commit": pre["code_commit"], "code_identity": pre["code_identity"],
                "snapshot": pre["holdout_snapshot"]["content_sha256"], "runtime_s": round(_time.perf_counter() - t0, 1)})
    (out_dir / "result.json").write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    return res


# ---------------------------------------------------------------------------------------------- finalize
def decide(study: str, results: Mapping[str, Mapping[str, Any]], threshold: float) -> Tuple[bool, List[str]]:
    """The YAML's pass_rule. Undefined statistics fail."""
    def finite(x: Any) -> bool:
        return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

    p = results["primary"]["summary"]
    t, m = p.get("t_cluster"), p.get("mean_net_r")
    prim = finite(t) and finite(m) and t >= threshold and m > 0
    reasons = [f"primary: t_cluster {t} (threshold {threshold}), mean net R {m} -> {'PASS' if prim else 'FAIL'}"]
    s2 = results["slippage_2_ticks"]["summary"].get("mean_net_r")
    sec = finite(s2) and s2 > 0
    reasons.append(f"slippage_2_ticks: mean net R {s2} > 0 -> {'PASS' if sec else 'FAIL'}")
    ok = prim and sec
    if study == "expiry_relief_long_v1":
        g = (results["primary"].get("drift_alpha") or {}).get("alpha_net")        # missing -> FAIL
        gate = finite(g) and g > 0
        reasons.append(f"second_gate: beta-adjusted drift alpha net of 0.28% = {g} > 0 -> {'PASS' if gate else 'FAIL'}")
        ok = ok and gate
    return bool(ok), reasons


def finalize(study: str, snapshots: Optional[Path] = None, register_path: Optional[Path] = None,
             records_dir: Optional[Path] = None) -> Dict[str, Any]:
    from research.decision import promotion

    cfg = _study(study)
    pre = preflight(study, snapshots)
    results = {}
    for part in cfg["parts"]:
        f = _out(study, part) / "result.json"
        if not f.exists():
            raise HoldoutRefused(f"part {part} has not run")
        r = json.loads(f.read_text(encoding="utf-8"))
        if (r["lock"] != pre["lock"] or r.get("code_identity") != pre["code_identity"]
                or r["snapshot"] != pre["holdout_snapshot"]["content_sha256"]):
            raise HoldoutRefused(f"part {part} ran under a different lock, code or snapshot")
        results[part] = r
    passed, reasons = decide(study, results, float(pre["spec"]["primary_test"]["threshold"]))
    sid = cfg["strategy_id"]
    record = {"strategy_id": sid, "variant": pre["spec"]["id"], "evidence_class": "E1",
              "prereg_sha256": pre["lock"]["yaml_sha256"], "passed": passed, "reasons": reasons,
              "pass_rule": cfg["pass_rule"],
              "parts": {p: {k: v for k, v in r.items() if k != "lock"} for p, r in results.items()},
              "window": results["primary"]["window"], "code_commit": pre["code_commit"],
              "code_identity": pre["code_identity"], "snapshot": pre["holdout_snapshot"]["content_sha256"],
              "finalized_at": datetime.now(IST).isoformat(timespec="seconds")}
    (_out(study) / "holdout_record.json").write_text(json.dumps(record, indent=1, default=str), encoding="utf-8")
    kw: Dict[str, Any] = {}
    if register_path:
        kw["register_path"] = register_path
    if records_dir:
        kw["records_dir"] = records_dir
    reg = promotion.load_register(register_path or promotion.REGISTER_PATH)
    if sid not in reg["strategies"]:
        promotion.register_candidate(sid, spec_path(study), cfg["notes"], **kw)
    decision = promotion.apply_holdout(sid, record, spec_path(study), **kw)
    _marker(study, "holdout_done").write_text(json.dumps({
        "finalized_at": record["finalized_at"], "passed": passed, "lock": pre["lock"], "code_commit": pre["code_commit"],
        "snapshot": record["snapshot"], "register_transition": decision["to"],
        "record_sha256": hashlib.sha256(json.dumps(record, sort_keys=True, default=str).encode()).hexdigest()},
        indent=1) + "\n", encoding="utf-8")
    return {"record": record, "decision": decision}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Event-study holdout run (exactly once)")
    ap.add_argument("cmd", choices=["lock", "preflight", "run", "finalize"])
    ap.add_argument("--study", required=True, choices=sorted(STUDIES))
    ap.add_argument("--part", default=None, help="one part (default: every part)")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "lock":
            print(json.dumps(write_lock(args.study), indent=1))
        elif args.cmd == "preflight":
            pre = preflight(args.study)
            print(json.dumps({k: v for k, v in pre.items() if k != "spec"}, indent=1, default=str))
        elif args.cmd == "run":
            for part in ([args.part] if args.part else list(_study(args.study)["parts"])):
                r = run_part(args.study, part)
                print(json.dumps({"part": part, "summary": r.get("summary"), "drift_alpha": r.get("drift_alpha"),
                                  "portfolio_3_slots": r.get("portfolio_3_slots"),
                                  "event_counts": r.get("event_counts")}, indent=1, default=str))
        else:
            out = finalize(args.study)
            print(json.dumps({"passed": out["record"]["passed"], "reasons": out["record"]["reasons"],
                              "register": out["decision"]["to"]}, indent=1))
    except HoldoutRefused as exc:
        print(f"REFUSED: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
