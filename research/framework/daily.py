"""
research/framework/daily.py
===========================
One command each evening after the NSE files land (after about 19:00 IST). PAPER ONLY.

    python -m research.framework.daily                    # today
    python -m research.framework.daily --date 2026-09-29

For every registered strategy (strategy.registered): check its rules (rules.strategy_problems); if clean, plan when
the day is a plan day, reconcile everything that finished, and summarise. Then write one report:
    shared/track2_liquid/paper/daily/<date>.json   and   <date>.md
The report also shows the market files for the day (present, chained to the previous session) and the state of the
NSE archive download (Antigravity's manifest: counts, blocks, pacing), so one file answers "is everything OK?".

Exit codes: 0 all clean; 1 a problem needs attention (read the report); 2 refused (research code not committed:
planning then would make every plan LATE, so nothing is written); 4 internal error.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from research.framework import desk, rules
from research.framework.market import MarketFiles
from research.framework.strategy import PaperStrategy, history_root, paper_root, registered

IST = timezone(timedelta(hours=5, minutes=30))
EXIT_OK, EXIT_ATTENTION, EXIT_REFUSED, EXIT_INTERNAL = 0, 1, 2, 4
MIN_GAP_S = 4.0


def market_status(md: MarketFiles, day: date) -> Dict[str, Any]:
    problems: List[str] = []
    notes: List[str] = []
    if day.weekday() >= 5:
        notes.append("weekend: no market files expected")
        return {"problems": problems, "notes": notes}
    has_cm, has_fo = md.cm_path(day).exists(), md.fo_path(day).exists()
    if not has_cm:
        problems.append(f"no CM file for {day} (a holiday, or the daily pipeline has not run)")
    if not has_fo:
        problems.append(f"no F&O file for {day}")
    if has_cm:
        prev = [d for d in md.sessions() if d < day]
        if prev:
            ch = md.chain(prev[-1], day)
            if ch["ok"] is False:
                problems.append(f"DATA_GAP: {day} does not follow {prev[-1]} ({ch['matched']}/{ch['compared']} "
                                "previous closes match): a session file is missing")
            elif ch["ok"] is None:
                problems.append(f"cannot verify that {day} follows {prev[-1]} ({ch.get('reason')})")
    if has_cm and md.surveillance_required(day) and md.surveillance(day) is None:
        problems.append(f"no readable ASM/GSM lists for {day} in history/raw/nse/surveillance "
                        "(a plan tonight would be BLOCKED)")
    nxt, skipped = md.entry_session(day)
    if nxt is None:
        notes.append("no ban list for the next session yet (a plan tonight would be BLOCKED)")
    else:
        notes.append(f"next session {nxt} (ban list published)" + (f"; skipped {skipped}" if skipped else ""))
    return {"problems": problems, "notes": notes}


def download_status(history: Path, day: date) -> Dict[str, Any]:
    """Read-only look at the NSE archive manifest written by the data program (Antigravity)."""
    p = Path(history) / "raw" / "nse_archive" / "manifest.jsonl"
    if not p.exists():
        return {"manifest": "absent"}
    rows, bad = [], 0
    for ln in p.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            try:
                rows.append(json.loads(ln))
            except ValueError:
                bad += 1
    out: Dict[str, Any] = {"lines": len(rows), "unreadable_lines": bad,
                           "outcomes": dict(Counter(r.get("outcome") for r in rows)),
                           "saved_by_dataset": dict(Counter(r.get("dataset") for r in rows if r.get("outcome") == "SAVED")),
                           "last_fetched_at": max((r.get("fetched_at", "") for r in rows), default=None),
                           "last_trade_date": max((r.get("trade_date", "") for r in rows
                                                   if r.get("outcome") == "SAVED"), default=None)}
    warn: List[str] = []
    if any(str(r.get("outcome", "")).startswith("STOPPED") or r.get("http_status") in (403, 429) for r in rows):
        warn.append("an HTTP 403/429 block is recorded: downloads must stop until the next day (data program rule 6)")
    # A SKIPPED_ALREADY_SAVED line made no request. Pacing is judged on request START times (requested_at); lines
    # written before that field existed are counted, not judged.
    today = [r for r in rows if str(r.get("requested_at") or r.get("fetched_at", "")).startswith(day.isoformat())
             and r.get("outcome") != "SKIPPED_ALREADY_SAVED"]
    out["requests_on_day"] = len(today)
    timed = [r for r in today if r.get("requested_at")]
    out["rows_without_request_time"] = len(today) - len(timed)
    ts = sorted(datetime.fromisoformat(r["requested_at"]) for r in timed)
    gaps = [(b - a).total_seconds() for a, b in zip(ts[:-1], ts[1:])]
    out["min_request_gap_s"] = min(gaps) if gaps else None
    short = sum(1 for g in gaps if g < MIN_GAP_S)
    if short:
        warn.append(f"{short} gaps under {MIN_GAP_S:.0f} s between request start times (data program rule 4)")
    if today and not timed:
        warn.append("no request start times (requested_at) logged: pacing cannot be verified")
    out["position"] = rows[-1].get("trade_date") if rows else None
    out["warnings"] = warn
    return out


def archive_audit_status(history: Path, cache: Optional[Path] = None) -> Dict[str, Any]:
    """Claude's machine audit of the archive download (research/data/archive_audit.py), when a manifest exists."""
    if not (Path(history) / "raw" / "nse_archive" / "manifest.jsonl").exists():
        return {"verdict": None, "note": "no archive manifest"}
    from research.data import archive_audit, paths

    a = archive_audit.audit(history)
    return {"verdict": a["verdict"], "coverage": a.get("coverage"), "files_checked": a["files_checked"],
            "sessions": a["sessions"],
            "range": [a.get("first_trade_date"), a.get("last_trade_date")], "holidays": len(a["holidays"]),
            "problems": dict(Counter(p["kind"] for p in a["problems"])), "first_problems": a["problems"][:5]}


def _brief(rec: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in rec.items() if k not in ("data_files", "excluded", "prev_hash", "hash")}


def run(day: date, *, history: Optional[Path] = None, strategies: Optional[List[PaperStrategy]] = None,
        report_dir: Optional[Path] = None, now: Optional[datetime] = None, code: Optional[Dict[str, Any]] = None,
        check_git: bool = True, check_location: bool = True, write: bool = True,
        audit_cache: Optional[Path] = None) -> Dict[str, Any]:
    now_arg, code_arg = now, code                 # passed on as given; the desk never trusts them (A4, -002)
    now = now or datetime.now(IST)
    h = Path(history) if history else history_root()
    md = MarketFiles(h)
    code = code if code is not None else desk.code_state()
    strategies = strategies if strategies is not None else registered()
    rep: Dict[str, Any] = {"day": day.isoformat(), "run_at": now.isoformat(timespec="seconds"), "paper_only": True,
                           "code_identity": code["identity"], "code_dirty": code["dirty"],
                           "market": market_status(md, day), "download": download_status(h, day), "strategies": {}}
    rep["archive_audit"] = archive_audit_status(h, audit_cache)
    loaded = rules.block_broker_imports()          # A7: the runtime paper-only boundary
    if loaded:                                    # -002: a loaded SDK can no longer be stopped, so nothing runs
        rep["market"]["problems"].append(f"REFUSED: broker SDK modules already loaded in this process {loaded}; "
                                         "nothing was planned or scored (paper only, AGENTS.md Rule 1)")
    if not strategies:                            # CODEX-FRAMEWORK-001 A8: nothing checked is not "all clean"
        rep["market"]["problems"].append("no paper strategy is registered: nothing was planned or scored")
    exit_code = EXIT_ATTENTION if (rep["market"]["problems"] or rep["download"].get("warnings")
                                   or rep["archive_audit"].get("verdict") == "FAIL") else EXIT_OK
    if loaded:
        exit_code = EXIT_REFUSED
    for s in (strategies if not loaded else []):
        st: Dict[str, Any] = {"problems": rules.strategy_problems(s, check_git=check_git,
                                                                  check_location=check_location)}
        rep["strategies"][s.id] = st
        if st["problems"]:
            exit_code = max(exit_code, EXIT_ATTENTION)
            continue
        try:
            if s.is_plan_day(md, day):
                st["plan"] = _brief(desk.plan(s, md, day, now=now_arg, code=code_arg))
            st["reconcile"] = desk.reconcile(s, md, as_of=day, now=now_arg)
            st["summary"] = desk.summary(s)
        except Exception as exc:
            st["problems"].append(f"INTERNAL {type(exc).__name__}: {exc}")
            exit_code = EXIT_INTERNAL
            continue
        if st["reconcile"]["blocked"] or st["reconcile"]["overdue"] or st["summary"]["integrity"]:
            exit_code = max(exit_code, EXIT_ATTENTION)
        if st.get("plan") and st["plan"].get("status") != "OK":
            exit_code = max(exit_code, EXIT_ATTENTION)
    rep["exit_code"] = exit_code
    if write:
        out = Path(report_dir) if report_dir else paper_root() / "daily"
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{day.isoformat()}.json").write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
        (out / f"{day.isoformat()}.md").write_text(render(rep), encoding="utf-8")
    return rep


def render(rep: Dict[str, Any]) -> str:
    verdict = {0: "ALL CLEAN", 1: "ATTENTION NEEDED", 2: "REFUSED", 4: "INTERNAL ERROR"}[rep["exit_code"]]
    lines = [f"# Paper desk daily report {rep['day']}: {verdict}", "", f"Run at {rep['run_at']}. Paper only.", "",
             "## Market files"]
    lines += [f"- PROBLEM: {p}" for p in rep["market"]["problems"]] or ["- OK"]
    lines += [f"- {n}" for n in rep["market"]["notes"]]
    d = rep["download"]
    lines += ["", "## NSE archive download (data program)"]
    if d.get("manifest") == "absent":
        lines.append("- no manifest yet")
    else:
        lines.append(f"- {d['lines']} manifest lines; outcomes {d['outcomes']}; now at trade date {d['position']}; "
                     f"{d['requests_on_day']} requests on this day; shortest gap between request starts "
                     f"{d['min_request_gap_s']} s")
        lines += [f"- WARNING: {w}" for w in d.get("warnings", [])]
    a = rep.get("archive_audit", {})
    if a.get("verdict"):
        lines.append(f"- Claude's audit: {a['verdict']} ({a['files_checked']} files, {a['sessions']} CM sessions "
                     f"{a['range'][0]}..{a['range'][1]}, {a['holidays']} holidays)"
                     + (f"; problems {a['problems']}" if a["problems"] else ""))
        lines += [f"  - {json.dumps(p, default=str)[:300]}" for p in a.get("first_problems", [])]
    for sid, st in rep["strategies"].items():
        lines += ["", f"## {sid}"]
        lines += [f"- PROBLEM: {p}" for p in st["problems"]]
        if st.get("plan"):
            p = st["plan"]
            lines.append(f"- plan {p.get('plan_day')}: {p.get('status')} / {p.get('evidence')}"
                         + (f" ({p.get('inadmissible_reason')})" if p.get("inadmissible_reason") else "")
                         + (f": {len(p.get('signals', []))} signals, book {p.get('book')}" if p.get("status") == "OK"
                            else f": {p.get('reason')}"))
        if st.get("reconcile"):
            r = st["reconcile"]
            lines.append(f"- reconcile: {r['scored']} scored, {r['pending']} pending, {len(r['blocked'])} blocked, "
                         f"{len(r['overdue'])} overdue")
        if st.get("summary"):
            sm = st["summary"]
            pr = sm["PROSPECTIVE"]
            lines.append(f"- evidence: {pr['trades']} prospective trades over {pr['plan_days']} plan days, mean net R "
                         f"{pr['mean_net_r']}; decision {sm['decision']['decision']} ({sm['decision']['reason']})")
            lines += [f"- INTEGRITY: {x}" for x in sm["integrity"]] + [f"- WARNING: {x}" for x in sm["warnings"]]
    return "\n".join(lines) + "\n"


def main(argv: Optional[List[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Paper desk daily run (paper only)")
    ap.add_argument("--date", default=None)
    args = ap.parse_args(argv)
    try:
        day = date.fromisoformat(args.date) if args.date else datetime.now(IST).date()
        code = desk.code_state()
        if code["dirty"]:
            print(f"REFUSED: research code has uncommitted changes {code['dirty'][:5]}; commit first, then re-run "
                  "(a plan written now could never count as evidence)")
            return EXIT_REFUSED
        rep = run(day)                            # the desk reads the real clock and code state itself (A4)
        print(render(rep))
        return rep["exit_code"]
    except Exception as exc:
        print(f"INTERNAL ERROR {type(exc).__name__}: {exc}")
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())
