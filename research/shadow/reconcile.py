"""
research/shadow/reconcile.py
============================
End-of-session reconciliation of the shadow ledger (plan P8.2; Yashu's mandate of 26 Sep 2026).

    python -m research.shadow.reconcile            # writes shadow_reports/summary.json and summary.md

Per session: signals, allocations, every drop reason (slot, cluster, sector, aggregate cap, VIX size-zero,
SHADOW_LATE / SHADOW_UNJOURNALED ...), peak exposure against the Rs 1,14,000 cap, and the prospective E1
trades with their net R. Cumulative progress against the gates that exist, each labelled with its source:
  - AGENTS.md rule 1: at least 60 prospective sessions AND at least 20 realistically fillable entries before
    live trading is even considered;
  - plan decision 3: the terminal promotion test runs at n_pre = 111 ADMISSIBLE (E2/E3) trades; E1 shadow
    rows are progress, never admissible evidence;
  - Yashu's mandate cites 85 trades: that is the plan's power reference (delta = 0.20R at 80% power,
    independent trades), reported for orientation only.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from research.data import paths
from research.decision.sizing import AGGREGATE_EXPOSURE_CAP_RS

RULE1_SESSIONS, RULE1_FILLABLE = 60, 20
N_PRE, POWER_REF_N = 111, 85


def summarise(ledger_path: Path, reports_dir: Optional[Path] = None) -> Dict[str, Any]:
    from research.decision.ledger import Ledger

    rows = Ledger(ledger_path).rows(mode="SHADOW") if Path(ledger_path).exists() else []
    by_session: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_session[str(r["session"])].append(r)
    sessions: List[Dict[str, Any]] = []
    prospective_trades: List[float] = []
    fillable = 0
    for day in sorted(by_session):
        rs = by_session[day]
        disp = Counter(str(r["disposition"]).split(":")[0] if not str(r["disposition"]).startswith("DROPPED:")
                       else str(r["disposition"]).split(":", 1)[1] for r in rs)
        filled = [r for r in rs if r["allocated"] and r["evidence_class"] == "E1" and r.get("entry_fill_price")]
        fillable += len(filled)
        prospective_trades += [float(r["net_r"]) for r in filled if r.get("net_r") is not None]
        rep = {}
        if reports_dir and (Path(reports_dir) / f"{day}.json").exists():
            rep = (json.loads((Path(reports_dir) / f"{day}.json").read_text(encoding="utf-8")).get("allocation") or {})
        sessions.append({"session": day, "signals": len(rs), "allocated": sum(1 for r in rs if r["allocated"]),
                         "filled_e1": len(filled), "dispositions": dict(disp),
                         "peak_exposure_rs": rep.get("peak_exposure_rs"),
                         "peak_exposure_share_of_cap": (rep["peak_exposure_rs"] / AGGREGATE_EXPOSURE_CAP_RS)
                         if rep.get("peak_exposure_rs") is not None else None,
                         "book_flat": rep.get("book_flat"),
                         "net_r_filled": [float(r["net_r"]) for r in filled if r.get("net_r") is not None]})
    admissible = sum(1 for r in rows if r["evidence_class"] in ("E2", "E3"))
    n = len(prospective_trades)
    return {"sessions_observed": len(sessions), "signals": len(rows),
            "prospective_filled_e1": fillable,
            "mean_net_r_e1": (sum(prospective_trades) / n) if n else None,
            "admissible_e2_e3": admissible,
            "progress": {"rule1_sessions": f"{len(sessions)}/{RULE1_SESSIONS}",
                         "rule1_fillable_entries": f"{fillable}/{RULE1_FILLABLE}",
                         "plan_n_pre_admissible": f"{admissible}/{N_PRE}",
                         "power_reference_trades (orientation only)": f"{fillable}/{POWER_REF_N}"},
            "sessions": sessions}


def to_markdown(s: Mapping[str, Any]) -> str:
    lines = ["# Shadow reconciliation", "",
             "Paper/research only. E1 shadow trades are progress, not admissible evidence (plan P6.6).", "",
             f"- Sessions observed: {s['sessions_observed']}; signals {s['signals']}; "
             f"prospective filled E1 entries {s['prospective_filled_e1']}; admissible E2/E3 {s['admissible_e2_e3']}",
             f"- Progress: {s['progress']}", "",
             "| Session | Signals | Allocated | Filled E1 | Peak exposure (share of cap) | Book flat | Dispositions |",
             "|---|---|---|---|---|---|---|"]
    for r in s["sessions"]:
        share = r["peak_exposure_share_of_cap"]
        lines.append(f"| {r['session']} | {r['signals']} | {r['allocated']} | {r['filled_e1']} | "
                     f"{'' if share is None else f'{share:.0%}'} | {r['book_flat']} | {r['dispositions']} |")
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    from research.shadow.run_day import shadow_root

    ap = argparse.ArgumentParser(description="Shadow ledger reconciliation (paper/research only)")
    ap.add_argument("--ledger", default=str(shadow_root() / "shadow_ledger.db"))
    ap.add_argument("--reports", default=str(paths.shared_input("shadow_reports")))
    args = ap.parse_args(argv)
    s = summarise(Path(args.ledger), Path(args.reports))
    out = paths.ensure(Path(args.reports))
    (out / "summary.json").write_text(json.dumps(s, indent=1, default=str), encoding="utf-8")
    (out / "summary.md").write_text(to_markdown(s), encoding="utf-8")
    print(json.dumps({k: v for k, v in s.items() if k != "sessions"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
