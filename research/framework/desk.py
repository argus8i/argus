"""
research/framework/desk.py
==========================
The generic paper desk. PAPER ONLY (AGENTS.md Rule 1): no broker code, no orders. Every strategy goes through the
same rules here, so no strategy can grade its own homework:

  plan       one journal row per plan day. PROSPECTIVE only when ALL hold: kind PLAN, status OK, research code
             committed (not dirty), written before the entry deadline (09:00 IST on the entry session).
             Otherwise LATE (or REPLAY for replays), with the reason. Refused when the pre-registration differs
             from the one the first plan used. A second OK plan for the same day is a DUPLICATE and never scored.
             The entry session needs a published ban list; a skipped weekday that has a CM file blocks the plan.
  reconcile  scores each signal of the FIRST OK plan of a day exactly once, after all its hold sessions exist and
             only when the files chain from the plan day through the hold (no missing session: market.chain).
             Otherwise the plan stays pending (reported), or is BLOCKED with the reason (DATA_GAP,
             ENTRY_SESSION_MISSING, CHAIN_UNKNOWN). Plans still pending long after entry are reported OVERDUE.
  summary    per evidence class; VOID counts by reason; integrity problems (pre-registration changed); and the
             evaluation decision on PROSPECTIVE results only (evaluate.decide with the pre-registered numbers).
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from research.framework import evaluate
from research.framework.market import MarketFiles
from research.framework.strategy import PaperStrategy
from research.shadow.run_day import Journal

IST = timezone(timedelta(hours=5, minutes=30))
EVIDENCE = ("PROSPECTIVE", "LATE", "REPLAY")
PLAN_KINDS = ("PLAN", "PLAN_REPLAY")
VOID_RATE_WARN = 0.20


def code_state() -> Dict[str, Any]:
    """Identity of the research code and any uncommitted change to it. Unknown counts as dirty (fail closed)."""
    try:
        from research.studies.run_holdout import code_state as cs

        st = cs()
        return {"identity": st["identity"], "dirty": list(st["dirty"]), "head": st.get("head")}
    except Exception as exc:
        return {"identity": f"UNKNOWN ({type(exc).__name__})", "dirty": ["UNKNOWN"]}


def plan_day_of(row: Dict[str, Any]) -> Optional[str]:
    return row.get("plan_day") or row.get("expiry")          # journals written before the framework used "expiry"


def _prereg_sha(strategy: PaperStrategy) -> str:
    from research.studies import prereg_io

    return prereg_io.normalised_sha256(strategy.prereg_path())


# ---------------------------------------------------------------------------------------------- plan
def plan(strategy: PaperStrategy, md: MarketFiles, day: date, journal_path: Optional[Path] = None, *,
         now: Optional[datetime] = None, kind: str = "PLAN", code: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if kind not in PLAN_KINDS:
        raise ValueError(f"kind must be one of {PLAN_KINDS}")
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        raise ValueError("now must carry a timezone (a naive time cannot be compared with the 09:00 IST deadline)")
    journal = Journal(journal_path or strategy.journal_path(replay=kind != "PLAN"))
    rows = journal.verify()
    code = code if code is not None else code_state()
    sha = _prereg_sha(strategy)
    base = {"kind": kind, "strategy": strategy.id, "plan_day": day.isoformat(),
            "written_at": now.isoformat(timespec="seconds"), "prereg_sha256": sha,
            "code_identity": code["identity"], "code_dirty": bool(code["dirty"])}

    earlier = {r.get("prereg_sha256") for r in rows if r.get("kind") == kind and r.get("prereg_sha256")}
    entry: Optional[date] = None
    if earlier and earlier != {sha}:
        p: Dict[str, Any] = {"status": "REFUSED", "reason": f"pre-registration changed after the first plan "
                                                            f"(journal {sorted(earlier)}, now {sha})"}
    else:
        entry, skipped = md.entry_session(day)
        traded = [d.isoformat() for d in skipped if md.cm_path(d).exists()]
        if entry is None:
            p = {"status": "BLOCKED", "reason": "no ban list published for any weekday in the 7 days after "
                                                f"{day} (fail closed)"}
        elif traded:
            p = {"status": "BLOCKED", "reason": f"skipped weekday(s) {traded} have a CM file: a session whose ban "
                                                "list is missing (fail closed)"}
            entry = None
        else:
            p = dict(strategy.build_plan(md, day, entry))
            p["skipped_weekdays"] = [d.isoformat() for d in skipped]
        prior = [r["seq"] for r in rows if r.get("kind") == kind and plan_day_of(r) == day.isoformat()
                 and r.get("status") == "OK"]
        if p.get("status") == "OK" and prior:
            p = {"status": "DUPLICATE", "reason": f"an OK plan for {day} already exists (seq {prior[0]})"}

    deadline = datetime.combine(entry, strategy.entry_deadline, IST) if entry else None
    reason = None
    if p.get("status") != "OK":
        reason = "NOT_OK"
    elif code["dirty"]:
        reason = "CODE_DIRTY"
    elif deadline is None or now >= deadline:
        reason = "AFTER_DEADLINE"
    admissible = kind == "PLAN" and reason is None
    rec = {**base, "entry_session": entry.isoformat() if entry else None,
           "deadline": deadline.isoformat() if deadline else None, "admissible": admissible,
           "evidence": "REPLAY" if kind != "PLAN" else ("PROSPECTIVE" if admissible else "LATE"), **p}
    if kind == "PLAN" and reason:
        rec["inadmissible_reason"] = reason
    journal.append([rec])
    return journal.verify()[-1]


# ---------------------------------------------------------------------------------------------- reconcile
def reconcile(strategy: PaperStrategy, md: MarketFiles, journal_path: Optional[Path] = None, *, as_of: date,
              replay: bool = False, now: Optional[datetime] = None) -> Dict[str, Any]:
    journal = Journal(journal_path or strategy.journal_path(replay=replay))
    rows = journal.verify()
    now = now or datetime.now(IST)
    done = {(r["plan_seq"], r["symbol"]) for r in rows if r.get("kind") == "RESULT"}
    first_ok: Dict[Any, int] = {}
    for r in rows:
        if r.get("kind") in PLAN_KINDS and r.get("status") == "OK":
            first_ok.setdefault((r["kind"], plan_day_of(r)), r["seq"])
    ss = md.sessions()
    h = strategy.hold_sessions
    new: List[Dict[str, Any]] = []
    pending, blocked, overdue, ignored = 0, [], [], []
    for pr in rows:
        if pr.get("kind") not in PLAN_KINDS or pr.get("status") != "OK":
            continue
        if first_ok[(pr["kind"], plan_day_of(pr))] != pr["seq"]:
            ignored.append(pr["seq"])
            continue
        entry = date.fromisoformat(pr["entry_session"])
        todo = [s for s in pr["signals"] if (pr["seq"], s["symbol"]) not in done]
        if not todo:
            continue
        hold = [d for d in ss if d >= entry][:h]
        if len(hold) < h or hold[-1] > as_of:
            pending += len(todo)
            if (as_of - entry).days > 2 * h + 7:
                overdue.append({"plan_seq": pr["seq"], "entry_session": entry.isoformat(), "sessions_found": len(hold)})
            continue
        if hold[0] != entry:
            blocked.append({"plan_seq": pr["seq"], "reason": "ENTRY_SESSION_MISSING",
                            "detail": f"first session on file is {hold[0]}"})
            continue
        ch = md.chain_window([date.fromisoformat(plan_day_of(pr))] + hold)
        if ch["ok"] is not True:
            blocked.append({"plan_seq": pr["seq"], "reason": "DATA_GAP" if ch["ok"] is False else "CHAIN_UNKNOWN",
                            "detail": ch["breaks"] or ch["unknown"]})
            continue
        book = set(pr.get("book", pr.get("book_top3", [])))
        for s in todo:
            out = {"kind": "RESULT", "strategy": strategy.id, "plan_seq": pr["seq"], "symbol": s["symbol"],
                   "side": s.get("side"), "plan_day": plan_day_of(pr), "entry_session": entry.isoformat(),
                   "exit_session": hold[-1].isoformat(), "evidence": pr["evidence"], "in_book": s["symbol"] in book,
                   "scored_at": now.isoformat(timespec="seconds")}
            res = dict(strategy.score(md, pr, s, hold))
            if "exit_reason" not in res or "net_r" not in res:
                raise ValueError(f"{strategy.id}.score must return exit_reason and net_r")
            if res["net_r"] is not None and not math.isfinite(float(res["net_r"])):
                res = {**res, "exit_reason": "VOID_NON_FINITE", "net_r": None}
            new.append({**out, **res})
    if new:
        journal.append(new)
    return {"scored": len(new), "pending": pending, "blocked": blocked, "overdue": overdue,
            "ignored_duplicate_plans": ignored}


# ---------------------------------------------------------------------------------------------- summary
def summary(strategy: PaperStrategy, journal_path: Optional[Path] = None, *, replay: bool = False) -> Dict[str, Any]:
    rows = Journal(journal_path or strategy.journal_path(replay=replay)).verify()
    plans = [r for r in rows if r.get("kind") in PLAN_KINDS]
    res = [r for r in rows if r.get("kind") == "RESULT"]
    scored = [r for r in res if r.get("net_r") is not None]
    out: Dict[str, Any] = {}
    for ev in EVIDENCE:
        v = [r for r in scored if r.get("evidence") == ev]
        book = [r["net_r"] for r in v if r.get("in_book", r.get("in_book_top3"))]
        out[ev] = {"trades": len(v), "plan_days": len({plan_day_of(r) for r in v}),
                   "mean_net_r": round(float(np.mean([r["net_r"] for r in v])), 4) if v else None,
                   "book_trades": len(book), "book_mean_net_r": round(float(np.mean(book)), 4) if book else None}
    voids = Counter(r.get("exit_reason") for r in res if r.get("net_r") is None)
    out["void"] = sum(voids.values())
    out["void_reasons"] = dict(voids)
    out["plans"] = dict(Counter(f"{r['kind']}:{r.get('status')}:{r.get('evidence')}" for r in plans))
    integrity, warnings = [], []
    shas = {r.get("prereg_sha256") for r in plans if r.get("kind") == "PLAN" and r.get("prereg_sha256")}
    if len(shas) > 1:
        integrity.append(f"PREREG_CHANGED: plans used {len(shas)} different pre-registrations")
    if len(res) >= 10 and out["void"] / len(res) > VOID_RATE_WARN:
        warnings.append(f"VOID_RATE {out['void']}/{len(res)} above {VOID_RATE_WARN:.0%}: check the VOID reasons")
    out["integrity"], out["warnings"] = integrity, warnings
    prosp = [r for r in scored if r.get("evidence") == "PROSPECTIVE"]
    ev = strategy.evaluation
    out["decision"] = evaluate.decide([float(r["net_r"]) for r in prosp], [plan_day_of(r) for r in prosp],
                                      review_after=int(ev["review_after"]),
                                      futility_after=int(ev["futility_after"]), t_pass=float(ev.get("t_pass", 2.0)))
    return out
