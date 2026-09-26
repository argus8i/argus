"""
research/framework/backtest.py
==============================
Runs a plug-in over history with the SAME code the paper desk uses (desk.make_plan and desk.score_plan), so a
backtest and the paper desk can never disagree about what a rule means.

    python -m research.framework.backtest --strategy EXPIRY_RELIEF_LONG_v2 --from 2022-01-03 --to 2024-09-30 --dry-run
    python -m research.framework.backtest --strategy ... --from ... --to ... --register "what this run tests"

Window rule: plan days and every session a trade holds must lie inside the design window (2022-01-03..2024-09-30).
The sealed holdout (2024-10-01..2026-07-31) is read only once, by research/studies/event_holdout.py, and the time
after it is prospective paper evidence; a window touching either is REFUSED. A plan whose entry or hold would
cross the window end is counted as unfinished and never read.
Every design run that informs a decision is registered in research/evidence/trials_registry.csv (--register); a
registered run needs committed research code so it can be reproduced from the commit.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from research.framework import desk, evaluate
from research.framework.market import MarketFiles
from research.framework.strategy import PaperStrategy

DESIGN = (date(2022, 1, 3), date(2024, 9, 30))


class WindowRefused(PermissionError):
    """The requested window reaches outside the design window."""


def _stats(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    scored = [t for t in trades if t.get("net_r") is not None]
    v = [float(t["net_r"]) for t in scored]
    st = evaluate.cluster_stats(v, [t["plan_day"] for t in scored])
    by_year: Dict[str, List[float]] = defaultdict(list)
    for t in scored:
        by_year[t["plan_day"][:4]].append(float(t["net_r"]))
    book = [float(t["net_r"]) for t in scored if t.get("in_book")]
    sd = float(np.std(v, ddof=1)) if len(v) > 1 else float("nan")
    return {"n": st["n"], "plan_days": st["clusters"], "mean_net_r": st["mean"], "se": st["se"], "t": st["t"],
            "sr_per_trade": st["mean"] / sd if np.isfinite(sd) and sd > 0 else float("nan"),
            "by_year": {y: {"n": len(x), "mean_net_r": float(np.mean(x))} for y, x in sorted(by_year.items())},
            "years_positive": sum(1 for x in by_year.values() if np.mean(x) > 0), "years": len(by_year),
            "book_n": len(book), "book_mean_net_r": float(np.mean(book)) if book else None,
            "void": dict(Counter(t["exit_reason"] for t in trades if t.get("net_r") is None))}


def run(strategy: PaperStrategy, md: MarketFiles, start: date, end: date) -> Dict[str, Any]:
    if start > end or start < DESIGN[0] or end > DESIGN[1]:
        raise WindowRefused(f"{start}..{end} is outside the design window {DESIGN[0]}..{DESIGN[1]}; the holdout "
                            "is read once by event_holdout.py and later days are prospective paper evidence")
    plans: Counter = Counter()
    blocked: Counter = Counter()
    trades: List[Dict[str, Any]] = []
    unfinished = 0
    data_files: Dict[str, str] = {}
    for day in md.sessions():
        if not (start <= day <= end) or not strategy.is_plan_day(md, day):
            continue
        entry, _ = md.entry_session(day)
        if entry is not None and entry > end:
            unfinished += 1                                # never read the entry-day files past the window
            continue
        entry, p = desk.make_plan(strategy, md, day)
        plans[p.get("status")] += 1
        if p.get("status") != "OK" or not p.get("signals"):
            continue
        data_files.update(p.get("data_files", {}))
        pr = {**p, "plan_day": day.isoformat(), "entry_session": entry.isoformat()}
        state, got = desk.score_plan(strategy, md, pr, p["signals"], as_of=end)
        if state == "PENDING":
            unfinished += 1
            continue
        if state == "BLOCKED":
            blocked[got["reason"]] += 1
            continue
        hold, results = got
        book = set(p.get("book", []))
        for s, res in zip(p["signals"], results):
            trades.append({"plan_day": day.isoformat(), "entry_session": entry.isoformat(),
                           "exit_session": hold[-1].isoformat(), "symbol": s["symbol"], "side": s.get("side"),
                           "in_book": s["symbol"] in book, **res})
    ident = hashlib.sha256(json.dumps(sorted(data_files.items())).encode()).hexdigest()
    return {"strategy": strategy.id, "window": [start.isoformat(), end.isoformat()], "plans": dict(plans),
            "blocked": dict(blocked), "unfinished_plans": unfinished, "trades": trades, "summary": _stats(trades),
            "data_identity": ident, "data_files": len(data_files)}


def register_trial(bt: Dict[str, Any], *, variant: str, notes: str = "", registry: Optional[Path] = None,
                   source: str = "research/framework/backtest.py", agent: str = "claude") -> str:
    from research.data import paths

    reg = Path(registry) if registry else paths.repo_root() / "research" / "evidence" / "trials_registry.csv"
    raw = reg.read_bytes()
    eol = "\r\n" if b"\r\n" in raw else "\n"
    ids = [r["trial_id"] for r in csv.DictReader(raw.decode("utf-8").splitlines())]
    tid = f"T{max((int(i[1:]) for i in ids if i[1:].isdigit()), default=0) + 1:04d}"
    s = bt["summary"]

    def fmt(x: Any) -> str:
        return "" if x is None or not np.isfinite(x) else f"{x:.4f}"

    row = [tid, date.today().isoformat(), bt["strategy"], variant, f"{bt['window'][0]}..{bt['window'][1]}",
           f"live_history_{bt['data_identity'][:12]}", s["n"], fmt(s["mean_net_r"]), "trigger", fmt(s["sr_per_trade"]),
           source, agent, f"t by plan day {fmt(s['t'])}; {s['plan_days']} plan days; years positive "
                          f"{s['years_positive']}/{s['years']}; void {s['void']}; {notes}".strip()]
    with open(reg, "a", encoding="utf-8", newline="") as fh:
        if raw and not raw.endswith(b"\n"):
            fh.write(eol)
        csv.writer(fh, lineterminator=eol).writerow(row)
    return tid


def main(argv: Optional[List[str]] = None) -> int:
    from research.data import paths
    from research.framework.strategy import by_id, history_root

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Backtest a registered paper strategy on the design window")
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--register", metavar="VARIANT", help="register the run in the trials registry")
    g.add_argument("--dry-run", action="store_true", help="exploration only; nothing registered")
    args = ap.parse_args(argv)
    code = desk.code_state()
    if args.register and code["dirty"]:
        print(f"REFUSED: a registered trial needs committed research code; dirty: {code['dirty'][:5]}")
        return 2
    s = by_id(args.strategy)
    try:
        bt = run(s, MarketFiles(history_root()), date.fromisoformat(args.start), date.fromisoformat(args.end))
    except WindowRefused as exc:
        print(f"REFUSED: {exc}")
        return 2
    out = paths.ensure(paths.outputs_dir() / "backtest" / s.id / datetime.now().strftime("%Y%m%d_%H%M%S"))
    (out / "trades.jsonl").write_text("".join(json.dumps(t, default=str) + "\n" for t in bt["trades"]),
                                      encoding="utf-8")
    meta = {k: v for k, v in bt.items() if k != "trades"}
    meta["code"] = code
    if args.register:
        meta["trial_id"] = register_trial(bt, variant=args.register, source=str(out.relative_to(paths.repo_root())))
    (out / "summary.json").write_text(json.dumps(meta, indent=1, default=str), encoding="utf-8")
    print(json.dumps(meta, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
